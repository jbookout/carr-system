#!/usr/bin/env python3
"""
gate-baseline-cochange-selftest.py — the acceptance test for the gate/baseline
co-change half of ops/githooks/pre-commit, written before the check itself
(rule e65efc68, enforcing rule c0b38d80).

WHAT THE CHECK IS FOR. The blessed baseline (ops/config/gate-baseline.json) is
the only thing that lets a session distinguish "a gate changed on purpose" from
"something disabled a gate". Rule c0b38d80 says the re-bless rides in the SAME
commit as the gate change, and it has been violated twice for real:

  2026-08-12  a gate change landed without its bless, and main's baseline was
              later overwritten by a PR cut before it (repaired in 6e2be66).
  2026-08-14  a live hotfix to hooks/settings-change-gate.py sat uncommitted
              and unblessed in the shared checkout; every session that morning
              started under a GATE INTEGRITY FAILURE banner it could do
              nothing about.

Both incidents are the same shape: the gate moved, the baseline did not, and
every later session had to treat the enforcement layer as untrusted. A commit
is the one moment where the two can be forced to move together.

WHAT THE CHECK MUST DO:

  1. REFUSE a staged change to any hooks/*.py gate file when
     ops/config/gate-baseline.json is not staged with it.
  2. REFUSE when the baseline IS staged but its recorded hash does not match
     the staged gate content — a bless of the wrong bytes is the 2026-08-13
     false re-bless, not a bless.
  3. ALLOW the same commit once the baseline is re-blessed and staged.
  4. IGNORE commits that touch no gate file and no wiring contract; ignore
     hooks/*-selftest.py, which gate-integrity deliberately does not protect.
  5. COVER the wiring contracts (rule-enforcement-map.json and friends) the
     same way — a contract changes enforcement without touching a hook.
  6. HAVE A WORKING ESCAPE HATCH, same idiom as its neighbours:
     CARR_ALLOW_UNBLESSED_GATE=1 for one command.
  7. NOT TOUCH the repository that invoked it (the ops/git_env.py leak).

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/gate-baseline-cochange-selftest.py

ops/ci.sh already globs ops/*-selftest.py, so this is enforced from the commit
that adds it, with no CI change.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "ops" / "githooks" / "pre-commit"
HELPER = REPO / "ops" / "githooks" / "staged-gate-bless-check.py"

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
    e = dict(os.environ)
    # THE LEAK GUARD, same as main-commit-gate-selftest.py: git hands every hook
    # a GIT_DIR pointing at the repository that invoked it.
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_COMMON_DIR", "GIT_NAMESPACE"):
        e.pop(var, None)
    if env:
        e.update(env)
    p = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True, env=e)
    if check_rc and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_baseline(repo, hashes: dict, contracts: dict | None = None):
    path = repo / "ops" / "config" / "gate-baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_note": "fixture baseline",
        "blessed_by": "fixture@example.invalid",
        "blessed_at_rev": "0000000",
        "hashes": hashes,
        "contracts": contracts or {},
    }, indent=2) + "\n")
    return path


def make_repo(tmp):
    """A throwaway repo on a feature branch (the main guard is not under test
    here) with the hook and its helper installed through core.hooksPath."""
    repo = Path(tmp) / "fixture"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feature/gates", check_rc=True)
    git(repo, "config", "user.email", "fixture@example.invalid", check_rc=True)
    git(repo, "config", "user.name", "Fixture", check_rc=True)
    hooks = repo / "githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    shutil.copy(HELPER, hooks / "staged-gate-bless-check.py")
    git(repo, "config", "core.hooksPath", "githooks", check_rc=True)

    (repo / "hooks").mkdir()
    gate = repo / "hooks" / "demo-gate.py"
    gate.write_text("print('v1')\n")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v1')\n")})
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A", check_rc=True)
    git(repo, "commit", "-q", "-m", "seed", check_rc=True)
    return repo


print("\nops/githooks/pre-commit — a gate change carries its bless (c0b38d80)")

for required, what in ((HOOK, "hook"), (HELPER, "helper")):
    if not required.exists():
        print(f"  FAIL  the {what} does not exist at {required}")
        print("\n1 check(s) failed: missing file")
        sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    head0 = git(repo, "rev-parse", "HEAD").stdout.strip()

    # ── 1. a gate edit without the baseline is refused ──────────────────────
    (repo / "hooks" / "demo-gate.py").write_text("print('v2')\n")
    git(repo, "add", "hooks/demo-gate.py")
    r = git(repo, "commit", "-m", "edit gate without bless")
    check("a gate edit without the baseline is refused", r.returncode != 0,
          f"exit {r.returncode}")
    check("and nothing was committed",
          git(repo, "rev-parse", "HEAD").stdout.strip() == head0)
    msg = (r.stdout + r.stderr).lower()
    check("the refusal names the unblessed file", "demo-gate.py" in msg)
    check("the refusal says how to bless", "--bless" in msg)
    check("the refusal names its escape hatch", "carr_allow_unblessed_gate" in msg)
    check("the refused work is still staged",
          "hooks/demo-gate.py" in git(repo, "diff", "--cached", "--name-only").stdout)

    # ── 2. a stale bless is not a bless ─────────────────────────────────────
    write_baseline(repo, {"demo-gate.py": sha256_of("print('WRONG')\n")})
    git(repo, "add", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "edit gate with mismatched bless")
    check("a baseline whose hash does not match the staged gate is refused",
          r.returncode != 0, f"exit {r.returncode}")
    check("the mismatch refusal names the file",
          "demo-gate.py" in (r.stdout + r.stderr).lower())

    # ── 3. the matching bless lets the same commit through ──────────────────
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v2')\n")})
    git(repo, "add", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "edit gate with real bless")
    check("gate edit plus matching baseline is allowed", r.returncode == 0,
          r.stderr)
    check("and it landed", git(repo, "rev-parse", "HEAD").stdout.strip() != head0)

    # ── 4. a NEW gate file needs a bless too ────────────────────────────────
    (repo / "hooks" / "new-gate.py").write_text("print('new')\n")
    git(repo, "add", "hooks/new-gate.py")
    r = git(repo, "commit", "-m", "new gate without bless")
    check("a new gate file without a baseline entry is refused",
          r.returncode != 0, f"exit {r.returncode}")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v2')\n"),
                          "new-gate.py": sha256_of("print('new')\n")})
    git(repo, "add", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "new gate with bless")
    check("a new gate file with its baseline entry is allowed",
          r.returncode == 0, r.stderr)

    # ── 5. contracts are gates by another name ──────────────────────────────
    contract = repo / "ops" / "config" / "rule-enforcement-map.json"
    contract.write_text('{"rules": 1}\n')
    git(repo, "add", "ops/config/rule-enforcement-map.json")
    r = git(repo, "commit", "-m", "contract without bless")
    check("a wiring-contract edit without the baseline is refused",
          r.returncode != 0, f"exit {r.returncode}")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v2')\n"),
                          "new-gate.py": sha256_of("print('new')\n")},
                   {"rule-enforcement-map.json": sha256_of('{"rules": 1}\n')})
    git(repo, "add", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "contract with bless")
    check("a wiring-contract edit with its bless is allowed",
          r.returncode == 0, r.stderr)

    # ── 6. what the check must leave alone ──────────────────────────────────
    (repo / "unrelated.txt").write_text("hello\n")
    git(repo, "add", "unrelated.txt")
    r = git(repo, "commit", "-m", "unrelated change")
    check("a commit touching no gate is allowed", r.returncode == 0, r.stderr)

    (repo / "hooks" / "demo-selftest.py").write_text("print('test')\n")
    git(repo, "add", "hooks/demo-selftest.py")
    r = git(repo, "commit", "-m", "selftest change")
    check("hooks/*-selftest.py is not treated as a gate", r.returncode == 0,
          r.stderr)

    # ── 7. the escape hatch works ───────────────────────────────────────────
    (repo / "hooks" / "demo-gate.py").write_text("print('v3')\n")
    git(repo, "add", "hooks/demo-gate.py")
    r = git(repo, "commit", "-m", "hatch",
            env={"CARR_ALLOW_UNBLESSED_GATE": "1"})
    check("CARR_ALLOW_UNBLESSED_GATE=1 lets an unblessed gate commit through",
          r.returncode == 0, r.stderr)

    # ── 8. a gate DELETION still requires the baseline to move ──────────────
    git(repo, "rm", "-q", "hooks/new-gate.py")
    r = git(repo, "commit", "-m", "delete gate without bless")
    check("deleting a gate without touching the baseline is refused",
          r.returncode != 0, f"exit {r.returncode}")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v3')\n")},
                   {"rule-enforcement-map.json": sha256_of('{"rules": 1}\n')})
    git(repo, "add", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "delete gate with bless")
    check("deleting a gate with the baseline updated is allowed",
          r.returncode == 0, r.stderr)

    # ── 9. the hook did not touch the repository that invoked it ────────────
    outer_status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                                  capture_output=True, text=True).stdout
    check("the outer repository gained no fixture files",
          "demo-gate.py" not in outer_status and "unrelated.txt" not in outer_status)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("GATE BASELINE CO-CHANGE SELFTEST PASSED: a gate or contract change moves "
      "only with a matching bless, everything else is untouched, and the escape "
      "hatch works.")
