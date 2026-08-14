#!/usr/bin/env python3
"""Self-test the changed-file semantic security gate in throwaway git repos."""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

GATE = pathlib.Path(__file__).with_name("ci-semantic-security.py")
RESULTS = []


def check(label, condition, detail=""):
    RESULTS.append(bool(condition))
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f" ({detail})" if detail and not condition else ""))


def run(repo, *args):
    env = dict(os.environ)
    for name in ("CARR_CI_BASE_SHA", "CI_BASE_SHA", "GITHUB_BASE_SHA", "GITHUB_EVENT_BEFORE"):
        env.pop(name, None)
    return subprocess.run([sys.executable, str(GATE), "--repo", str(repo), *args],
                          capture_output=True, text=True, timeout=30, env=env)


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def write(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def policy(repo, high_risk, rules):
    path = repo / "policy.json"
    path.write_text(json.dumps({"version": 1, "high_risk_patterns": high_risk, "rules": rules}), encoding="utf-8")
    return path


def new_repo(root):
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "semantic@example.invalid")
    git(repo, "config", "user.name", "Semantic selftest")
    write(repo, "README", "base\n")
    commit(repo, "initial")
    return repo


def main():
    with tempfile.TemporaryDirectory(prefix="semantic-security-") as temp:
        root = pathlib.Path(temp)

        # A harmless changed file must not be made guilty by existing risky code.
        repo = new_repo(root / "unrelated")
        write(repo, "infra/auth.py", "historical security debt\n")
        commit(repo, "old risky code")
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "notes.txt", "unrelated edit\n")
        commit(repo, "notes only")
        p = policy(repo, ["infra/**"], [{"id": "auth", "patterns": ["infra/**"], "required_tests": ["test-auth"]}])
        result = run(repo, "--strict", "--base", base, "--policy", str(p))
        check("clean unrelated change is NO_CANDIDATES", result.returncode == 0 and "NO_CANDIDATES" in result.stdout, result.stdout + result.stderr)

        repo = new_repo(root / "accounted")
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "infra/auth.py", "changed auth\n")
        commit(repo, "auth change")
        p = policy(repo, ["infra/**"], [{"id": "auth", "patterns": ["infra/**"], "required_tests": ["test-auth"]}])
        result = run(repo, "--strict", "--base", base, "--policy", str(p), "--evidence", "test-auth")
        check("matching candidate with mapped evidence is ACCOUNTED", result.returncode == 0 and "ACCOUNTED" in result.stdout, result.stdout + result.stderr)

        result = run(repo, "--strict", "--base", base, "--policy", str(p))
        check("matching candidate without completed evidence is REFUSED",
              result.returncode == 1 and "required test evidence missing" in result.stderr,
              result.stdout + result.stderr)

        repo = new_repo(root / "unmapped")
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "infra/auth.py", "changed auth\n")
        commit(repo, "unmapped risk")
        p = policy(repo, ["infra/**"], [])
        result = run(repo, "--strict", "--base", base, "--policy", str(p))
        check("unmapped high-risk candidate is REFUSED", result.returncode == 1 and "REFUSED" in result.stderr and "no mapped tests" in result.stderr, result.stdout + result.stderr)

        repo = new_repo(root / "file-limit")
        base = git(repo, "rev-parse", "HEAD")
        for index in range(101):
            write(repo, f"ordinary/{index}.txt", "x\n")
        commit(repo, "many files")
        p = policy(repo, ["infra/**"], [{"id": "auth", "patterns": ["infra/**"], "required_tests": ["test-auth"]}])
        result = run(repo, "--strict", "--base", base, "--policy", str(p))
        check("changed-file limit is REFUSED", result.returncode == 1 and "limit exceeded" in result.stderr, result.stdout + result.stderr)

        repo = new_repo(root / "line-limit")
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "ordinary.txt", "x\n" * 5001)
        commit(repo, "many lines")
        p = policy(repo, ["infra/**"], [{"id": "auth", "patterns": ["infra/**"], "required_tests": ["test-auth"]}])
        result = run(repo, "--strict", "--base", base, "--policy", str(p))
        check("diff-line limit is REFUSED", result.returncode == 1 and "diff-line limit exceeded" in result.stderr, result.stdout + result.stderr)

        repo = new_repo(root / "missing-base")
        p = policy(repo, ["infra/**"], [{"id": "auth", "patterns": ["infra/**"], "required_tests": ["test-auth"]}])
        result = run(repo, "--strict", "--policy", str(p))
        check("strict mode refuses a missing base", result.returncode == 1 and "no base supplied" in result.stderr, result.stdout + result.stderr)

        result = run(repo, "--strict", "--base", "not-a-local-commit", "--policy", str(p))
        check("strict mode refuses an untrusted base", result.returncode == 1 and "untrusted base" in result.stderr, result.stdout + result.stderr)

        repo = new_repo(root / "rename-risk")
        write(repo, "notes.txt", "becomes risky\n")
        commit(repo, "ordinary source")
        base = git(repo, "rev-parse", "HEAD")
        (repo / "migrations").mkdir()
        (repo / "notes.txt").rename(repo / "migrations" / "0117.sql")
        commit(repo, "rename into high risk")
        p = policy(repo, ["migrations/**"], [{"id": "migration", "patterns": ["migrations/**"], "required_tests": ["migration-safety"]}])
        result = run(repo, "--strict", "--base", base, "--policy", str(p))
        check("rename into a high-risk path cannot evade accounting",
              result.returncode == 1 and "required test evidence missing" in result.stderr,
              result.stdout + result.stderr)

        repo = new_repo(root / "only-semantic")
        (repo / "ops" / "config").mkdir(parents=True)
        shutil.copy2(GATE, repo / "ops" / "ci-semantic-security.py")
        shutil.copy2(GATE.with_name("ci.sh"), repo / "ops" / "ci.sh")
        policy(repo, ["infra/**"], [{"id": "auth", "patterns": ["infra/**"],
                                     "required_tests": ["worker-contract"]}]).replace(
            repo / "ops" / "config" / "semantic-security-policy.json")
        commit(repo, "install gate fixture")
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "infra/auth.py", "changed auth\n")
        commit(repo, "high risk change")
        env = dict(os.environ)
        env["CARR_CI_BASE_SHA"] = base
        (repo / ".tmp").mkdir()
        env["TMPDIR"] = str(repo / ".tmp")
        result = subprocess.run(["bash", "ops/ci.sh", "--only", "semantic"], cwd=repo,
                                capture_output=True, text=True, timeout=30, env=env)
        check("--only semantic refuses high-risk work without prior evidence classes",
              result.returncode == 1 and "REFUSED" in (result.stdout + result.stderr)
              and "every class green" not in result.stdout,
              result.stdout + result.stderr)

    print(f"\nsemantic security selftest: {sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
