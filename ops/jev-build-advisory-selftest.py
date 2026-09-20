#!/usr/bin/env python3
"""Offline contract for automatic Jev participation in build sessions."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


advisory = load("jev_build_advisory_test", REPO / "ops/jev_build_advisory.py")
lint = load("lint_gate_build_protocol_test", REPO / "hooks/lint-gate.py")


class FakeClient:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}

    def ask(self, state, questions, timeout):
        self.state = state
        self.questions = questions
        self.timeout = timeout
        return {
            "model": "jev-test",
            "answers": {
                key: {"type": "noul", "noul": (0.91 if i % 2 == 0 else 0.13)}
                for i, key in enumerate(questions)
            },
            "usage": {"input_tokens": 20, "output_tokens": 6},
        }


class AdvisoryTests(unittest.TestCase):
    def test_one_batched_request_returns_every_typed_facet(self):
        client = FakeClient()
        result = advisory.advise("Design and verify the change", client=client)
        self.assertEqual(result["schema"], "jev-build-advisory/v1")
        self.assertEqual(result["model"], "jev-test")
        self.assertEqual(set(result["facets"]), set(advisory.FACETS))
        self.assertEqual(set(client.questions),
                         set(advisory.FACETS) | set(advisory.GUIDANCE_TEXT))
        self.assertEqual(set(result["guidance"]), set(advisory.GUIDANCE_TEXT))
        self.assertEqual(client.state, {"partner_request": "Design and verify the change"})
        self.assertEqual(result["authority"], "advisory_only")
        self.assertIn("permissions_and_authority", result["deterministic_exclusions"])
        self.assertEqual(
            [row["facet"] for row in result["required_actions"]],
            [facet for i, facet in enumerate(advisory.FACETS) if i % 2 == 0])
        self.assertTrue(all("Jev" in row["instruction"]
                            for row in result["required_actions"]))

    def test_missing_or_invalid_answers_are_unavailable(self):
        class Broken(FakeClient):
            def ask(self, state, questions, timeout):
                row = super().ask(state, questions, timeout)
                row["answers"][advisory.FACETS[0]]["noul"] = 1.2
                return row
        with self.assertRaises(advisory.AdvisoryUnavailable):
            advisory.advise("judge this", client=Broken())

    def test_unavailable_is_fixed_visible_and_redacted(self):
        failure = advisory.unavailable()
        self.assertEqual(failure["schema"], "jev-build-advisory-unavailable/v1")
        self.assertEqual(failure["effect"], "visible_advisory_abstention")
        self.assertNotIn("error", failure)


class EditCoverageTests(unittest.TestCase):
    def test_patch_target_extraction_covers_codex_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch = "*** Begin Patch\n*** Update File: src/a.py\n*** Move to: src/b.py\n*** End Patch"
            payload = {"tool_name": "functions.apply_patch", "cwd": tmp,
                       "tool_input": {"command": patch}}
            self.assertEqual(lint._changed_code_paths(payload), [
                str(Path(tmp) / "src/a.py"), str(Path(tmp) / "src/b.py")])

    def test_both_clients_wire_intake_and_post_edit_review(self):
        claude = json.loads((REPO / "ops/config/hooks.json").read_text())
        codex = json.loads((REPO / "ops/config/codex-hooks.json").read_text())["hooks"]
        intake = "hooks/rule-pack-preuse-reselection.py"
        review = "hooks/lint-gate.py"
        for label, config in (("Claude", claude), ("Codex", codex)):
            prompt_rows = [row for row in config["UserPromptSubmit"]
                           if any(intake in h.get("command", "")
                                  for h in row.get("hooks", []))]
            review_rows = [row for row in config["PostToolUse"]
                           if any(review in h.get("command", "")
                                  for h in row.get("hooks", []))]
            self.assertEqual(len(prompt_rows), 1, label)
            self.assertEqual(len(review_rows), 1, label)
            self.assertIn("apply_patch", review_rows[0].get("matcher", ""), label)
            self.assertNotIn("functions\\.exec", review_rows[0].get("matcher", ""), label)

    def test_unsupported_file_emits_a_validated_bound_receipt(self):
        out = io.StringIO()
        payload = {
            "tool_name": "Write", "cwd": str(REPO),
            "session_id": "session-review", "tool_use_id": "tool-review",
            "tool_input": {"file_path": str(REPO / "README.md")},
        }
        with contextlib.redirect_stdout(out):
            lint.code_review(payload)
        unsupported = json.loads(
            json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(unsupported["paths"][0]["reason"], "unsupported_extension")
        contract = load("rule_delivery_postwrite_test",
                        REPO / "lib/rule_delivery_preuse.py")
        self.assertTrue(contract.validate_postwrite_receipt(unsupported, repo=REPO))
        forged = dict(unsupported)
        forged["tool_input_sha256"] = "0" * 64
        self.assertFalse(contract.validate_postwrite_receipt(forged, repo=REPO))


if __name__ == "__main__":
    unittest.main()
