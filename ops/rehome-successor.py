#!/usr/bin/env python3
"""Preflight successor ownership and merge clean branches.

Full seal regeneration is pending complete database catalog-row evidence; this
command refuses any reallocation rather than inventing a combined seal.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from git_env import scrubbed_env
from successor_ownership import domain_bytes, is_owned_file

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from integration_candidate import allocation_plan


class RehomeError(ValueError):
    pass


def git(repo: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, env=scrubbed_env(),
                            capture_output=True, timeout=120)
    if result.returncode not in allowed:
        raise RehomeError(f"Git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def file_at(repo: Path, revision: str, path: str) -> bytes | None:
    if not git(repo, "ls-tree", revision, "--", path).strip():
        return None
    return git(repo, "show", f"{revision}:{path}")


def conflicts(repo: Path, approved: str, main: str) -> list[str]:
    result = git(repo, "merge-tree", "--write-tree", "-z", approved, main,
                 allowed=(0, 1))
    records = result.split(b"\0")[1:]
    paths = set()
    for record in records:
        if not record:
            break
        if b"\t" not in record:
            raise RehomeError("cannot read merge conflict paths")
        paths.add(record.split(b"\t", 1)[1].decode("utf-8"))
    return sorted(paths)


def owned_conflict(repo: Path, base: str, approved: str, main: str, path: str) -> bool:
    before = file_at(repo, base, path)
    ours = file_at(repo, approved, path)
    theirs = file_at(repo, main, path)
    if is_owned_file(path, before, ours):
        return True
    if before is None or ours is None or theirs is None:
        return False
    return domain_bytes(path, before) == domain_bytes(path, ours) == domain_bytes(path, theirs)


def manifest(repo: Path, git_dir: Path, approved: str, main: str, rewritten: list[str]) -> Path:
    target = git_dir / "successor-rehome.json"
    data = {
        "schema": "successor-rehome/v1", "approved_sha": approved,
        "main_sha": main, "new_sha": git(repo, "rev-parse", "HEAD").decode().strip(),
        "rewritten_paths": sorted(rewritten),
    }
    temporary = git_dir / "successor-rehome.json.tmp"
    with temporary.open("w") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return target


def rehome(repo: Path) -> Path:
    repo = repo.resolve(strict=True)
    root = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if repo != root:
        raise RehomeError("pass the worktree root")
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).decode().strip()
    if not branch or branch == "main":
        raise RehomeError("use an isolated feature branch")
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").decode().strip())
    with (git_dir / "successor-rehome.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RehomeError("another successor rehome owns this worktree") from None
        if git(repo, "status", "--porcelain").strip():
            raise RehomeError("commit the worktree changes before rehome")
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            if (git_dir / name).exists():
                raise RehomeError(f"unfinished Git operation: {name}")
        git(repo, "fetch", "--quiet", "origin", "main")
        main = git(repo, "rev-parse", "origin/main").decode().strip()
        approved = git(repo, "rev-parse", "HEAD").decode().strip()
        bases = git(repo, "merge-base", "--all", approved, main).decode().splitlines()
        if len(bases) != 1:
            raise RehomeError("rehome requires one merge base")
        base = bases[0]
        conflict_paths = conflicts(repo, approved, main)
        refused = [p for p in conflict_paths if not owned_conflict(repo, base, approved, main, p)]
        if refused:
            raise RehomeError("conflict outside successor ownership: " + ", ".join(refused))
        if conflict_paths:
            raise RehomeError("successor regeneration needs complete database catalog rows: " + ", ".join(conflict_paths))
        added = git(repo, "diff", "--diff-filter=A", "--name-only", base, approved,
                    "--", "migrations", "mcp-server/src").decode().splitlines()
        successors = [p for p in added if is_owned_file(p, None, file_at(repo, approved, p))]
        if successors:
            pending = [Path(p).name for p in added if p.startswith("migrations/") and p.endswith(".sql")]
            plan = allocation_plan(repo, main, pending)
            moves = [p for p in pending if plan["migration_names"][p] != p]
            expected = f'mcp-server/src/scac-mutation-registry.v{plan["registry_successor"]}.generated.js'
            moves.extend(p for p in successors if ".v" in p and p != expected)
            if moves:
                raise RehomeError("successor regeneration needs complete database catalog rows: " + ", ".join(moves))
        result = subprocess.run(["git", "merge", "--no-ff", "--no-commit", main],
                                cwd=repo, env=scrubbed_env(), capture_output=True, timeout=120)
        if result.returncode:
            if (git_dir / "MERGE_HEAD").exists():
                git(repo, "merge", "--abort")
            raise RehomeError("merge changed since preflight; no automatic retry")
        if (git_dir / "MERGE_HEAD").exists():
            message = git_dir / "successor-rehome-message"
            message.write_text("Merge current main for successor integration\n")
            try:
                git(repo, "commit", "-F", str(message))
            except RehomeError:
                git(repo, "merge", "--abort")
                raise
        return manifest(repo, git_dir, approved, main, [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worktree", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps({"ok": True, "manifest": str(rehome(args.worktree))}, sort_keys=True))
        return 0
    except (RehomeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"successor rehome refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
