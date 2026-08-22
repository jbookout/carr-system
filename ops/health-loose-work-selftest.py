#!/usr/bin/env python3
"""Hermetic regression cases for Health's shared loose-work classifier."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import health_submodule as health  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
        (repo / "seed").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "seed"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
        worktree = repo / ".codex-worktrees" / "known"
        worktree.parent.mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "known", str(worktree)], check=True)
        daemon = repo / "tools" / "doc-convo" / "assets"
        daemon.mkdir(parents=True)
        (daemon / "render-daemon.log").write_text("generated\n", encoding="utf-8")

        buckets = health.classify_loose_status(repo, [
            "?? .codex-worktrees/known/",
            "?? tools/doc-convo/assets/render-daemon.log",
            "?? pipelines/new-source.py",
            " M tools/health-check.py",
        ])
        require(buckets["managed_artifacts"] == [
                    ".codex-worktrees/known/", "tools/doc-convo/assets/render-daemon.log"],
                f"managed: {buckets}")
        require(buckets["actionable_untracked"] == ["pipelines/new-source.py"],
                f"unknown untracked must remain actionable: {buckets}")
        require(buckets["actionable_tracked"] == ["tools/health-check.py"],
                f"tracked: {buckets}")

        uncertain = health.classify_loose_status(repo, ["?? .codex-worktrees/unknown/"])
        require(uncertain["actionable_untracked"] == [".codex-worktrees/unknown/"],
                f"an unverified worktree root must remain actionable: {uncertain}")

        root_worktrees = repo / ".worktrees"
        root_child = root_worktrees / "known"
        root_worktrees.mkdir()
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "root-known", str(root_child)], check=True)
        root_status = health.classify_loose_status(repo, ["?? .worktrees/"])
        require(root_status["managed_artifacts"] == [".worktrees/"],
                f"a nonempty all-worktree root is managed: {root_status}")

        (repo / ".codex-worktrees" / "Graph-System").mkdir()
        codex_root = health.classify_loose_status(repo, ["?? .codex-worktrees/"])
        require(codex_root["actionable_untracked"] == [".codex-worktrees/"],
                f"a mixed or non-Git Codex root must remain actionable: {codex_root}")

        arbitrary = repo / ".worktrees" / "arbitrary"
        arbitrary.mkdir()
        (arbitrary / ".git").write_text("gitdir: /not-a-registered-worktree\n", encoding="utf-8")
        forged = health.classify_loose_status(repo, ["?? .worktrees/arbitrary/"])
        require(forged["actionable_untracked"] == [".worktrees/arbitrary/"],
                f"a dangling .git link must not be treated as a managed worktree: {forged}")

        require(health.loose_work_requires_attention(
                    {"actionable_tracked": [], "actionable_untracked": ["new.py"],
                     "expected_patched_submodules": [], "managed_artifacts": []}),
                "unknown untracked source must require health attention")
        clean: dict[str, list[str]] = {
            "actionable_tracked": [], "actionable_untracked": [],
            "expected_patched_submodules": [], "managed_artifacts": []}
        require(not health.loose_work_requires_attention(clean),
                "a clean loose-work bucket must not require attention")
        require(health.loose_work_requires_attention(
                    {**clean, "actionable_tracked": ["edited.py"]}),
                "canonical tracked work requires attention by default")
        recent_tracked = {"actionable_tracked": ["edited.py"], "actionable_untracked": [],
                          "expected_patched_submodules": [], "managed_artifacts": []}
        require(not health.loose_work_requires_attention(recent_tracked, False),
                "recent tracked-only work remains within recovery's grace period")
        recent_plus_unknown = {**recent_tracked, "actionable_untracked": ["new.py"]}
        require(health.loose_work_requires_attention(recent_plus_unknown, False),
                "unknown untracked source overrides the recent-tracked grace period")

        submodule = repo / "vendor" / "quill"
        (submodule / ".git").mkdir(parents=True)
        patches = repo / "patches"
        patches.mkdir()
        diff = """diff --git a/a b/a
--- a/a
+++ b/a
@@ -1 +1 @@
-old
+new
"""
        (patches / "0001.patch").write_text(diff, encoding="utf-8")
        with patch.object(health.subprocess, "run",
                          return_value=SimpleNamespace(stdout=diff)):
            expected = health.classify_loose_status(repo, [" M vendor/quill"])
        require(expected["expected_patched_submodules"] == ["vendor/quill"],
                f"expected patched submodule must be non-actionable: {expected}")

    source = (ROOT / "tools" / "health-check.py").read_text(encoding="utf-8")
    require(source.count("classify_loose_status(") == 2,
            "canonical and recovery loose-work consumers must share one classifier")
    require(source.count("loose_work_requires_attention(") >= 2,
            "canonical and recovery consumers must share the actionable verdict")
    print("health loose-work selftest: passed")


if __name__ == "__main__":
    main()
