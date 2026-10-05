#!/usr/bin/env python3
"""Check that the transcript extractor refuses private or complex inputs."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("extract_real_turns", ROOT / "tools/ruleprecision-real-turns.py")
assert SPEC is not None and SPEC.loader is not None
EXTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXTRACT)
setattr(EXTRACT, "TRACKED", {"ops/ci.sh", "ops/config/rule-routes.v1.json"})


class PrivacyBoundary(unittest.TestCase):
    def test_credentials_and_client_words_are_not_emitted(self):
        self.assertIsNone(EXTRACT.safe_prompt("review code for ExampleClinic"))
        self.assertIsNone(EXTRACT.safe_prompt("fix code authorization bearer sample"))
        self.assertIsNone(EXTRACT.safe_call("Bash", {"command": "git status; printenv"}, pathlib.Path("/repo")))
        self.assertIsNone(EXTRACT.safe_call("Bash", {"command": "gh pr list --head ExampleClinic"}, pathlib.Path("/repo")))

    def test_source_paths_require_tracked_control_code(self):
        self.assertIsNone(EXTRACT.safe_call("Read", {"file_path": "/repo/out/private.json"}, pathlib.Path("/repo")))
        self.assertIsNone(EXTRACT.safe_call("Read", {"file_path": "/elsewhere/ops/ci.sh"}, pathlib.Path("/repo")))
        self.assertEqual(EXTRACT.safe_call("Read", {"file_path": "/repo/ops/ci.sh"}, pathlib.Path("/repo"))[0],
                         {"tool_name": "Read", "tool_input": {"file_path": "<repo>/ops/ci.sh"}})

    def test_original_safe_action_is_preserved(self):
        command = "git diff --stat origin/main"
        self.assertEqual(EXTRACT.safe_call("Bash", {"command": command}, pathlib.Path("/repo"))[0]["tool_input"]["command"], command)
        self.assertEqual(EXTRACT.safe_prompt("not just claude or codex, every session no matter what model"),
                         "not just claude or codex, every session no matter what model")

    def test_control_read_input_is_not_a_general_json_escape(self):
        self.assertIsNotNone(EXTRACT.safe_call("mcp__carr__standing-context", {"detail": "boot", "page": 1}, pathlib.Path("/repo")))
        self.assertIsNone(EXTRACT.safe_call("mcp__carr__standing-context", {"query": "ExampleClinic"}, pathlib.Path("/repo")))
        self.assertIsNone(EXTRACT.safe_call("mcp__carr__read-loop", {"loop_id": "sensitive phrase"}, pathlib.Path("/repo")))


if __name__ == "__main__":
    unittest.main()
