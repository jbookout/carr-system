"""Synthetic integration cases for the public-source identity guard."""
import hashlib
import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))
import pii_guard
from git_env import fixture_env


class PublicSourceGuardTests(unittest.TestCase):
    def test_snapshot_export_sanitizes_prose_preserving_sql_and_row_identity(self):
        salt = "d" * 64
        corpus = {"schema": "public-source-identities/v1", "salt": salt,
                  "hashes": [hashlib.sha256((salt + "\0exampledentalgroup").encode()).hexdigest()],
                  "max_tokens": 3}
        source = ("select 1; -- Example Dental Group\n"
                  "COMMENT ON TABLE public.demo IS 'Example Dental Group example';\n"
                  "COPY public.retrieval_proposal (id, candidate, reason) FROM stdin;\n"
                  '00000000-0000-4000-8000-000000000001\t{"phrase":"Example Dental Group"}\tprose\n'
                  "\\.\n")
        result = pii_guard.sanitize_snapshot(source, corpus)
        self.assertNotIn("Example Dental Group", result)
        self.assertIn("select 1; -- Example Organization ", result)
        self.assertIn("00000000-0000-4000-8000-000000000001", result)
        self.assertIn('"phrase":"Example Organization ', result)
        self.assertEqual(pii_guard.sanitize_snapshot(result, corpus), result)
        with self.assertRaises(ValueError):
            pii_guard.sanitize_snapshot("select 'Example Dental Group';\n", corpus)
        with self.assertRaises(ValueError):
            pii_guard.sanitize_snapshot(
                "COPY public.party (name) FROM stdin;\nExample Dental Group\n\\.\n", corpus)
        multiline = "COMMENT ON TABLE public.demo IS 'Metadata\nExample Dental Group';\n"
        self.assertNotIn("Example Dental Group", pii_guard.sanitize_snapshot(multiline, corpus))
        self.assertEqual(pii_guard.sanitize_snapshot("select 'safe -- text';\n", corpus),
                         "select 'safe -- text';\n")

    def test_ci_scanner_checks_index_bytes_and_changed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "ops" / "config").mkdir(parents=True)
            for name in ["ci-secret-scan.py", "pii_guard.py", "git_env.py"]:
                shutil.copy(REPO / "ops" / name, root / "ops" / name)
            salt = "c" * 64
            corpus = {"schema": "public-source-identities/v1", "salt": salt,
                      "hashes": [hashlib.sha256((salt + "\0exampledentalgroup").encode()).hexdigest()],
                      "max_tokens": 3}
            (root / "ops/config/public-source-identities.v1.json").write_text(json.dumps(corpus))
            env = fixture_env()
            def git(*args):
                subprocess.run(["git", *args], cwd=root, env=env, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def scan(*args):
                return subprocess.run([sys.executable, "ops/ci-secret-scan.py", *args],
                                      cwd=root, env=env, capture_output=True, text=True)
            git("init", "-b", "main")
            git("config", "user.name", "Synthetic Tester")
            git("config", "user.email", "test@example.invalid")
            (root / "fixture.txt").write_text("Synthetic Clinic\n")
            git("add", "ops/ci-secret-scan.py", "ops/pii_guard.py", "ops/git_env.py",
                "ops/config/public-source-identities.v1.json", "fixture.txt")
            git("commit", "--no-verify", "-m", "synthetic baseline")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            (root / "fixture.txt").write_text("Example Dental Group\n")
            git("add", "fixture.txt")
            (root / "fixture.txt").write_text("Synthetic Clinic\n")
            result = scan("--staged")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stderr, "fixture.txt:1\n")
            self.assertNotIn("Example Dental Group", result.stdout + result.stderr)
            git("add", "fixture.txt")
            self.assertEqual(scan("--staged").returncode, 0)
            (root / "fixture.txt").write_text("EXAMPLE-DENTAL GROUP\n")
            self.assertEqual(scan().returncode, 1)
            git("add", "fixture.txt")
            git("commit", "--no-verify", "-m", "synthetic planted name")
            self.assertEqual(scan("--range", "origin/main..HEAD").returncode, 1)
            self.assertEqual(scan().returncode, 1)

    def test_normalization_across_punctuation_accents_and_lines(self):
        salt = "b" * 64
        corpus = {"schema": "public-source-identities/v1", "salt": salt,
                  "hashes": [hashlib.sha256((salt + "\0exampledentalgroup").encode()).hexdigest()],
                  "max_tokens": 3}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "corpus.json"
            path.write_text(json.dumps(corpus))
            output = io.StringIO()
            self.assertEqual(pii_guard.check(
                [("example.txt", "Éxample-Dental\nGROUP\nClean text")],
                path, output=output), 1)
            self.assertEqual(output.getvalue(), "example.txt:1\n")

    def test_invalid_or_missing_corpus_refuses_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "corpus.json"
            for raw in [None, "not-json", '{}', '{"hashes": []}']:
                if raw is not None:
                    path.write_text(raw)
                output = io.StringIO()
                self.assertEqual(pii_guard.check([], path, output=output), 2)
                self.assertEqual(output.getvalue(), "ops/config/public-source-identities.v1.json:1\n")

    def test_planted_name_fails_with_location_only_and_clean_file_passes(self):
        salt = "a" * 64
        # Independent worked vector: lowercase ASCII with separators removed.
        digest = hashlib.sha256((salt + "\0" + "exampledentalgroup").encode()).hexdigest()
        corpus = {"schema": "public-source-identities/v1", "salt": salt,
                  "hashes": [digest], "max_tokens": 3}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "corpus.json"
            path.write_text(json.dumps(corpus))
            output = io.StringIO()
            self.assertEqual(pii_guard.check(
                [("fixture.json", '{\n  "name": "EXAMPLE Dental Group"\n}')],
                path, output=output), 1)
            self.assertEqual(output.getvalue(), "fixture.json:2\n")
            output = io.StringIO()
            self.assertEqual(pii_guard.check(
                [("fixture.json", '{"name": "Synthetic Clinic"}')],
                path, output=output), 0)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
