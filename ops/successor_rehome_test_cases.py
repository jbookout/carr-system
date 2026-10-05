#!/usr/bin/env python3
"""Exercise successor recovery and approval carry-forward through their CLIs."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class SuccessorCommands(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="successor-rehome-test-"))
        self.env = fixture_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("domain.txt", "before\n")
        self.commit("domain.txt")
        self.base = self.head()
        self.git("remote", "add", "origin", str(self.repo))
        self.git("fetch", "-q", "origin")
        self.git("switch", "-qc", "feature")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo,
                                       env=self.env, text=True, stderr=subprocess.DEVNULL).strip()

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, *paths):
        self.git("add", "--", *paths)
        message = self.repo / ".git" / "fixture-message"
        message.write_text("Fixture change\n")
        self.git("commit", "-q", "-F", str(message))

    def head(self):
        return self.git("rev-parse", "HEAD")

    def command(self, name, *args):
        return subprocess.run([sys.executable, str(ROOT / "ops" / name), *args],
                              cwd=self.repo, env=self.env, capture_output=True, text=True)

    def advance_main(self, path="main.txt", content="main advanced\n"):
        self.git("switch", "-q", "main")
        self.write(path, content)
        self.commit(path)
        self.main = self.head()
        self.git("switch", "-q", "feature")

    def test_clean_rehome_preserves_domain_and_merge_parents(self):
        self.write("domain.txt", "feature\n")
        self.commit("domain.txt")
        approved = self.head()
        self.advance_main()
        result = self.command("rehome-successor.py", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("show", "HEAD:domain.txt"), "feature")
        self.assertEqual(self.git("rev-parse", "HEAD^1"), approved)
        self.assertEqual(self.git("rev-parse", "HEAD^2"), self.main)
        manifest = json.loads((self.repo / ".git" / "successor-rehome.json").read_text())
        self.assertEqual(manifest["rewritten_paths"], [])
        self.assertEqual(manifest["approved_sha"], approved)
        self.assertEqual(manifest["main_sha"], self.main)
        self.assertEqual(manifest["new_sha"], self.head())
        checked = self.command("successor-only-diff.py", approved, self.head())
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_domain_conflict_refuses_with_filename_and_preserves_head(self):
        self.write("domain.txt", "feature\n")
        self.commit("domain.txt")
        approved = self.head()
        self.advance_main("domain.txt", "main\n")
        result = self.command("rehome-successor.py", str(self.repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("domain.txt", result.stderr)
        self.assertEqual(self.head(), approved)
        self.assertEqual((self.repo / "domain.txt").read_text(), "feature\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.repo / ".git" / "successor-rehome.json").exists())

    def test_checker_accepts_generated_changes_and_domain_migration_rename(self):
        self.write("migrations/0749_feature.sql", "select 'domain';\n")
        self.write("mcp-server/src/scac-mutation-registry.v98.generated.js", "old generated\n")
        self.commit("migrations/0749_feature.sql", "mcp-server/src/scac-mutation-registry.v98.generated.js")
        approved = self.head()
        self.git("mv", "migrations/0749_feature.sql", "migrations/0750_feature.sql")
        self.write("mcp-server/src/scac-mutation-registry.v98.generated.js", "new generated\n")
        self.commit("migrations/0750_feature.sql",
                    "mcp-server/src/scac-mutation-registry.v98.generated.js")
        result = self.command("successor-only-diff.py", approved, self.head())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_checker_rejects_domain_migration_content_change(self):
        self.write("migrations/0749_feature.sql", "select 'domain';\n")
        self.commit("migrations/0749_feature.sql")
        approved = self.head()
        self.git("mv", "migrations/0749_feature.sql", "migrations/0750_feature.sql")
        self.write("migrations/0750_feature.sql", "select 'changed behavior';\n")
        self.commit("migrations/0750_feature.sql")
        result = self.command("successor-only-diff.py", approved, self.head())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("feature.sql", result.stdout + result.stderr)

    def test_checker_rejects_domain_edit_in_mixed_bookkeeping_file(self):
        self.write("bin/schema-snapshot.sh", "echo domain-before\n")
        self.commit("bin/schema-snapshot.sh")
        approved = self.head()
        self.write("bin/schema-snapshot.sh", "echo domain-after\n")
        self.commit("bin/schema-snapshot.sh")
        result = self.command("successor-only-diff.py", approved, self.head())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bin/schema-snapshot.sh", result.stdout + result.stderr)
