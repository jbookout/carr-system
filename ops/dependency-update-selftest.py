#!/usr/bin/env python3
"""Exercise the lock-update repair through its command-line interface."""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = pathlib.Path("control-room/contracts/fixtures/execution-fabric/assurance-compiler.valid.v1.json")
SCRIPT = ROOT / "ops/dependency-update-repin.py"


class LockRepairTests(unittest.TestCase):
    def test_changed_lock_requires_compiler_repin_and_repair_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = json.loads((ROOT / FIXTURE).read_text())
            lock = root / "requirements.lock"
            lock.write_text("pytest==9.1.1\n")
            target = root / FIXTURE
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(fixture, indent=2) + "\n")
            before = target.read_bytes()
            command = [sys.executable, str(SCRIPT), "--repo", str(root)]
            check = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(check.returncode, 1, check.stderr)
            self.assertEqual(target.read_bytes(), before)
            repair = subprocess.run(command + ["--write"], capture_output=True, text=True)
            self.assertEqual(repair.returncode, 0, repair.stderr)
            result = json.loads(repair.stdout)
            self.assertEqual(result["compiler_ok"], True)
            self.assertEqual(result["changed"], True)
            updated = json.loads(target.read_text())
            contract = updated["assurance_slice"]
            self.assertEqual(contract["required_tests"][0]["environment"]["dependency_lock"]["digest"],
                             "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest())
            self.assertNotEqual(contract["ownership_contract_digest"], fixture["assurance_slice"]["ownership_contract_digest"])
            self.assertNotEqual(contract["contract_digest"], fixture["assurance_slice"]["contract_digest"])
            self.assertEqual(contract["required_tests"][0]["test_artifact"], fixture["assurance_slice"]["required_tests"][0]["test_artifact"])
            repaired = target.read_bytes()
            again = subprocess.run(command + ["--write"], capture_output=True, text=True)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(json.loads(again.stdout)["changed"], False)
            self.assertEqual(target.read_bytes(), repaired)

    def test_compiler_refusal_cannot_write_the_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "requirements.lock").write_text("pytest==9.1.1\n")
            fixture = json.loads((ROOT / FIXTURE).read_text())
            fixture["applicable_rules"]["snapshot_digest"] = "sha256:" + "0" * 64
            target = root / FIXTURE
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(fixture))
            before = target.read_bytes()
            result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(root), "--write"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target.read_bytes(), before)

    def test_policy_keeps_security_immediate_and_majors_outside_groups(self):
        config = json.loads((ROOT / "renovate.json").read_text())
        self.assertEqual(set(config["enabledManagers"]), {"npm", "pip_requirements", "github-actions"})
        self.assertEqual(config["automerge"], False)
        self.assertEqual(config["platformAutomerge"], False)
        self.assertEqual(config["osvVulnerabilityAlerts"], True)
        self.assertEqual(config["vulnerabilityAlerts"]["schedule"], [])
        self.assertEqual(config["vulnerabilityAlerts"]["automerge"], False)
        groups = [rule["matchUpdateTypes"] for rule in config["packageRules"] if rule.get("groupName")]
        self.assertEqual(groups, [["patch"], ["minor"]])
        self.assertEqual(config["postUpgradeTasks"]["commands"], ["python3 ops/dependency-update-repin.py --write"])
        self.assertIn("/(^|/)requirements\\.lock$/", config["pip_requirements"]["managerFilePatterns"])

    def test_current_workflow_actions_are_immutable(self):
        import re
        uses = []
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for match in re.finditer(r"(?m)^\s*(?:-\s+)?uses:\s*(\S+)", workflow.read_text()):
                action = match.group(1)
                if action.startswith(("./", "docker://")):
                    continue
                uses.append(action)
                self.assertRegex(action, r"^[\w./-]+@[0-9a-f]{40}$", str(workflow))
        self.assertTrue(uses)


if __name__ == "__main__":
    unittest.main()
