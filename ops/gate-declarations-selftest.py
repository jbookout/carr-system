#!/usr/bin/env python3
"""Check gate declarations through the same projection interface as consumers."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.gate_declarations import DEFAULT, check, paired_selftest, render_hooks, replay_hooks, validate


class DeclarationTests(unittest.TestCase):
    def test_tracked_hook_projections_match_the_declaration(self):
        for source in json.loads(DEFAULT.read_text())["sources"]:
            with self.subTest(source=source):
                expected = json.loads((REPO / source).read_text())
                self.assertEqual(render_hooks(source=source), expected.get("hooks", expected))

    def test_renderer_orders_groups_and_hooks_and_keeps_replay_details(self):
        source = "ops/config/hooks.json"
        declaration = {
            "sources": [source],
            "hooks": {
                "alpha.py": {"role": "gate", "paired_selftest": "ops/alpha-selftest.py", "wirings": [
                    {"event": "PreToolUse", "matcher": "Bash", "fixtures": ["shell"], "executions": [
                        {"source": source, "group": 1, "order": 1, "matcher_present": True,
                         "type": "command", "command": "python3 hooks/alpha.py", "timeout": 7}]},
                    {"event": "Stop", "matcher": "", "fixtures": ["stop"], "executions": [
                        {"source": source, "group": 0, "order": 0, "matcher_present": False,
                         "type": "command", "command": "python3 hooks/alpha.py"}]}]},
                "beta.py": {"role": "gate", "paired_selftest": None, "unpaired_reason": "synthetic",
                            "wirings": [
                    {"event": "PreToolUse", "matcher": "Bash", "fixtures": ["shell"], "executions": [
                        {"source": source, "group": 1, "order": 0, "matcher_present": True,
                         "type": "command", "command": "python3 hooks/beta.py"}]},
                    {"event": "PreToolUse", "matcher": "Read", "fixtures": ["read"], "executions": [
                        {"source": source, "group": 0, "order": 0, "matcher_present": True,
                         "type": "command", "command": "python3 hooks/beta.py"}]}]},
            },
        }
        expected = {
            "PreToolUse": [
                {"matcher": "Read", "hooks": [{"type": "command", "command": "python3 hooks/beta.py"}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 hooks/beta.py"},
                                               {"type": "command", "command": "python3 hooks/alpha.py", "timeout": 7}]},
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "python3 hooks/alpha.py"}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "declarations.json"
            path.write_text(json.dumps(declaration))
            self.assertEqual(render_hooks(path), expected)
            self.assertEqual(replay_hooks(path), {
                "alpha.py": {"role": "gate", "wirings": [
                    {"event": "PreToolUse", "matcher": "Bash", "fixtures": ["shell"]},
                    {"event": "Stop", "matcher": "", "fixtures": ["stop"]}]},
                "beta.py": {"role": "gate", "wirings": [
                    {"event": "PreToolUse", "matcher": "Bash", "fixtures": ["shell"]},
                    {"event": "PreToolUse", "matcher": "Read", "fixtures": ["read"]}]},
            })

    def test_pair_aliases_and_existing_unpaired_hooks(self):
        self.assertEqual(paired_selftest("hooks/conduct-stop-gate.py"), "ops/conduct-gate-selftest.py")
        self.assertEqual(paired_selftest("guard-unattended.py"), "ops/guard-selftest.py")
        self.assertEqual(paired_selftest("chat-lint-carryover.py"), "ops/chat-lint-gate-selftest.py")
        self.assertIsNone(paired_selftest("cmd_text.py"))
        self.assertEqual(check(), [])

    def test_bad_declarations_are_rejected_at_the_consumer_interface(self):
        original = json.loads(DEFAULT.read_text())
        cases = []

        def case(label, edit, reason):
            changed = copy.deepcopy(original)
            edit(changed)
            cases.append((label, changed, reason))

        gate = "rule-boot-gate.py"
        case("absent hook", lambda d: d["hooks"].__setitem__("absent.py", {"role": "helper"}), "declared hook absent")
        case("missing declaration", lambda d: d["hooks"].pop(gate), "missing declaration")
        case("bad pairing", lambda d: d["hooks"][gate].__setitem__("paired_selftest", "ops/absent-selftest.py"), "invalid paired selftest")
        case("missing wiring", lambda d: d["hooks"][gate]["wirings"][0].__setitem__("executions", []), "no execution")
        case("changed command", lambda d: d["hooks"][gate]["wirings"][0]["executions"][0].__setitem__("command", "python3 hooks/other.py"), "different hook")
        case("changed event", lambda d: d["hooks"]["context-handoff-gate.py"]["wirings"][0].__setitem__("event", "Stop"), "wired event")
        case("bad timeout", lambda d: d["hooks"][gate]["wirings"][0]["executions"][0].__setitem__("timeout", 0), "invalid execution timeout")
        case("missing position", lambda d: d["hooks"][gate]["wirings"][0]["executions"][0].__setitem__("order", 2), "execution orders must be contiguous")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "declarations.json"
            for label, changed, reason in cases:
                with self.subTest(label=label):
                    path.write_text(json.dumps(changed))
                    self.assertTrue(any(reason in error for error in validate(path)), validate(path))

    def test_duplicate_hooks_and_changed_projection_are_rejected(self):
        text = DEFAULT.read_text()
        original = json.loads(text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "declarations.json"
            path.write_text(text.replace('"hooks": {', '"hooks": {"cmd_text.py": {},', 1))
            self.assertIn("duplicate declaration key", validate(path)[0])
            original["hooks"]["rule-boot-gate.py"]["wirings"][0]["matcher"] = "changed"
            path.write_text(json.dumps(original))
            self.assertTrue(any("projection mismatch" in error for error in check(path)))

    def test_malformed_declaration_is_a_reported_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "declarations.json"
            path.write_text("[]")
            self.assertEqual(validate(path), ["gate declarations must be an object"])

    def test_unknown_replay_scenario_set_is_rejected(self):
        original = json.loads(DEFAULT.read_text())
        original["hooks"]["rule-boot-gate.py"]["wirings"][0]["fixtures"] = ["absent-fixture-set"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "declarations.json"
            path.write_text(json.dumps(original))
            self.assertTrue(any("unknown fixture set" in error for error in check(path)))

    def fixture_repo(self, root):
        declarations = json.loads(DEFAULT.read_text())
        for name, entry in declarations["hooks"].items():
            hook = root / "hooks" / name
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("# synthetic public source fixture\n")
            if entry.get("paired_selftest"):
                pair = root / entry["paired_selftest"]
                pair.parent.mkdir(parents=True, exist_ok=True)
                pair.write_text("# synthetic public selftest fixture\n")
        for source in declarations["sources"]:
            target = root / source
            target.parent.mkdir(parents=True, exist_ok=True)
            hooks = render_hooks(source=source)
            if source == "ops/config/hooks.json":
                target.write_text(json.dumps(hooks))
            else:
                target.write_text(json.dumps({"hooks": hooks, "preserve_this_property": {"enabled": True}}))
        (root / "ops/config/gate-replay-manifest.json").write_text(
            (REPO / "ops/config/gate-replay-manifest.json").read_text())

    def writer(self, root, path=DEFAULT):
        return subprocess.run([sys.executable, str(REPO / "lib/gate_declarations.py"),
                               "--write", "--path", str(path), "--repo", str(root)],
                              capture_output=True, text=True, check=False)

    def test_writer_repairs_projections_without_changing_other_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_repo(root)
            target = root / ".claude/settings.json"
            before = json.loads(target.read_text())
            before["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 999
            target.write_text(json.dumps(before))
            self.assertTrue(check(repo=root))
            result = self.writer(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check(repo=root), [])
            self.assertEqual(json.loads(target.read_text())["preserve_this_property"], {"enabled": True})

    def test_invalid_declaration_cannot_write_any_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_repo(root)
            sources = json.loads(DEFAULT.read_text())["sources"]
            before = {source: (root / source).read_bytes() for source in sources}
            declarations = json.loads(DEFAULT.read_text())
            declarations["hooks"]["rule-boot-gate.py"]["wirings"][0]["executions"] = []
            path = root / "invalid.json"
            path.write_text(json.dumps(declarations))
            result = self.writer(root, path)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("no execution", result.stderr)
            self.assertEqual({source: (root / source).read_bytes() for source in sources}, before)

    def test_writer_refuses_unapproved_source_and_reads_all_files_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_repo(root)
            declarations = json.loads(DEFAULT.read_text())
            sources = declarations["sources"]
            before = {source: (root / source).read_bytes() for source in sources}
            declarations["sources"].append("../outside.json")
            path = root / "invalid-source.json"
            path.write_text(json.dumps(declarations))
            result = self.writer(root, path)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("repository hook configuration", result.stderr)
            self.assertEqual({source: (root / source).read_bytes() for source in before}, before)
            # A later unreadable source must prevent earlier projections from being rewritten.
            target = root / ".claude/settings.json"
            target.write_text("{")
            malformed_before = {source: (root / source).read_bytes() for source in before}
            result = self.writer(root)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("cannot read hook configuration before write", result.stderr)
            self.assertEqual({source: (root / source).read_bytes() for source in before}, malformed_before)


if __name__ == "__main__":
    unittest.main()
