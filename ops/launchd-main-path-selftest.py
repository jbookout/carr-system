#!/usr/bin/env python3
"""LaunchAgent runtime paths must never select a feature branch checkout."""
import importlib.util
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env
spec = importlib.util.spec_from_file_location("main_path_installer", ROOT / "ops/config-as-code.py")
assert spec is not None and spec.loader is not None
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LaunchdMainPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "canonical"
        self.feature = self.root / "feature"
        self.env = fixture_env()
        self.git("init", "-b", "main", str(self.repo))
        self.git("-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "seed")
        self.git("-C", str(self.repo), "worktree", "add", "-b", "repair", str(self.feature))
        for root in (self.repo, self.feature):
            (root / "ops").mkdir()
            (root / "ops/progress-board-render.sh").write_text("#!/bin/bash\n")
            (root / ".venv/bin").mkdir(parents=True)
            interpreter = root / ".venv/bin/python"
            interpreter.write_text("#!/bin/sh\n")
            interpreter.chmod(0o755)

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.env, check=True, capture_output=True, text=True)

    def test_template_rejects_each_feature_runtime_path(self):
        for field in ("WorkingDirectory", "Program", "ProgramArguments"):
            with self.subTest(field=field):
                data = {"Label": "local.test", "ProgramArguments": ["/bin/bash"], "WorkingDirectory": str(self.repo)}
                data[field] = ["/bin/bash", str(self.feature / "ops/progress-board-render.sh")] if field == "ProgramArguments" else str(self.feature) if field == "WorkingDirectory" else str(self.feature / "ops/progress-board-render.sh")
                refusal = installer.launchd_template_refusal(plistlib.dumps(data).decode())
                self.assertIsNotNone(refusal, f"accepted feature path in {field}")
                self.assertIn("main", refusal)
        safe = {"Label": "local.test", "ProgramArguments": ["/bin/bash", str(self.repo / "ops/progress-board-render.sh")], "WorkingDirectory": str(self.repo)}
        self.assertIsNone(installer.launchd_template_refusal(plistlib.dumps(safe).decode()))

    def test_symlink_and_missing_program_cannot_hide_feature_checkout(self):
        link = self.root / "runner"
        link.symlink_to(self.feature / "ops/progress-board-render.sh")
        for program in (link, self.feature / "ops/not-built-yet/runner.sh"):
            body = plistlib.dumps({"Label": "local.test", "ProgramArguments": [str(program)]}).decode()
            self.assertIsNotNone(installer.launchd_template_refusal(body))

    def test_dependency_checkout_on_its_release_branch_is_allowed(self):
        dependency = self.root / "dependency"
        self.git("init", "-b", "stable", str(dependency))
        body = plistlib.dumps({"Label": "local.test", "ProgramArguments": [str(dependency / "python")]}).decode()
        self.assertIsNone(installer.launchd_template_refusal(body))

    def test_board_installer_refuses_feature_checkout_without_writing(self):
        agents = self.root / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        dest = agents / "local.carr-progress-board.plist"
        old = {"Label": "local.carr-progress-board", "ProgramArguments": ["/bin/bash", "old.sh"], "StartInterval": 900}
        dest.write_bytes(plistlib.dumps(old))
        with patch.object(installer, "REPO", str(self.repo)), patch.object(installer, "HOME", str(self.root)), patch.object(installer, "install_launchd_plist", return_value="loaded") as install:
            self.assertEqual(installer.cmd_install_progress_board(True, repo=str(self.feature)), 1)
            install.assert_not_called()
        self.assertEqual(plistlib.loads(dest.read_bytes()), old)


if __name__ == "__main__":
    unittest.main()
