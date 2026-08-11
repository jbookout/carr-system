#!/usr/bin/env python3
"""Executable stage/finalize/rollback tests using fake external boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
PY = REPO / ".venv/bin/python"
CUTOFF = REPO / "tools/doctrine_cutoff.py"

IDS = {
    "monday": "11111111-1111-4111-8111-111111111111",
    "bootstrap_workspace": "22222222-2222-4222-8222-222222222222",
    "bootstrap_control": "22222222-2222-4222-8222-222222222223",
    "bootstrap_mature": "22222222-2222-4222-8222-222222222224",
    "stage": "33333333-3333-4333-8333-333333333333",
    "rollback_approval": "33333333-3333-4333-8333-333333333334",
    "cold": "44444444-4444-4444-8444-444444444444",
    "final": "55555555-5555-4555-8555-555555555555",
}


class CutoffLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"; self.vault.mkdir()
        self.evidence = self.root / "evidence"; self.evidence.mkdir()
        self.config = self.root / "config.json"
        self.sentinel = self.root / "sentinel.json"
        self.fake_home = self.root / "home"
        launch_dir = self.fake_home / "Library/LaunchAgents"; launch_dir.mkdir(parents=True)
        with (launch_dir / "com.carr.rules-refresh.plist").open("wb") as fh:
            plistlib.dump({"Label": "com.carr.rules-refresh",
                           "ProgramArguments": ["/bin/zsh", str(REPO / "bin/refresh-rules.sh")]}, fh)
        self.launch_state = self.root / "launch-state"
        self.launch_state.write_text("loaded")
        self.launchctl = self.root / "launchctl"
        self.launchctl.write_text(
            "#!/bin/sh\n"
            "state=\"$CARR_TEST_LAUNCH_STATE\"\n"
            "case \"$1\" in\n"
            " unload) rm -f \"$state\"; exit 0;;\n"
            " load) touch \"$state\"; exit 0;;\n"
            " print) test -f \"$state\"; exit $?;;\n"
            " *) exit 2;;\n"
            "esac\n")
        self.launchctl.chmod(0o755)
        self.env = {**os.environ,
                    "CARR_CUTOFF_TEST_MODE": "1",
                    "CARR_CUTOFF_CONFIG_FILE": str(self.config),
                    "CARR_CUTOFF_EVIDENCE_DIR": str(self.evidence),
                    "CARR_CUTOFF_SENTINEL": str(self.sentinel),
                    "CARR_CUTOFF_HOME": str(self.fake_home),
                    "CARR_CUTOFF_LAUNCHCTL": str(self.launchctl),
                    "CARR_TEST_LAUNCH_STATE": str(self.launch_state),
                    "CARR_CUTOFF_SMOKE_COMMANDS": json.dumps([["/usr/bin/true"]])}
        from tools.doctrine_cutoff_preflight import generated_markdown
        self.render_paths = generated_markdown(REPO)
        for rel in self.render_paths + ["CLAUDE.md", "AGENTS.md", "00_Context/today.md"]:
            path = self.vault / rel; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture {rel}\n")
        fixtures = {
            "monday": {"record_type": "finding", "verb": "record-system-evidence", "actor": "codex", "sponsoring_human": "joe",
                       "content": "Monday heartbeat completed store-first", "provenance": "live task readback"},
            "bootstrap_workspace": {"record_type": "doctrine_revision", "current": True, "actor": "codex",
                          "document": "carr-workspace-bduf", "content": "Fresh sessions call standing-context through the store",
                          "provenance": "write-doctrine-section"},
            "bootstrap_control": {"record_type": "doctrine_revision", "current": True, "actor": "codex",
                          "document": "carr-control-room-bduf", "content": "Fresh sessions call standing-context through the store",
                          "provenance": "write-doctrine-section"},
            "bootstrap_mature": {"record_type": "doctrine_revision", "current": True, "actor": "codex",
                          "document": "carr-mature-software-end-state-bduf", "content": "Fresh sessions call standing-context through the store",
                          "provenance": "write-doctrine-section"},
            "stage": {"record_type": "decision", "actor": "codex", "sponsoring_human": "joe",
                      "human_quote": "I approve the reversible doctrine cutoff stage",
                      "content": "approve cutoff stage", "provenance": "log-decision"},
            "rollback_approval": {"record_type": "decision", "actor": "codex", "sponsoring_human": "joe",
                      "human_quote": "I approve verified doctrine cutoff rollback",
                      "content": "approve cutoff rollback", "provenance": "log-decision"},
            "cold": {"record_type": "finding", "verb": "record-system-evidence", "actor": "codex", "sponsoring_human": "joe",
                     "content": "fresh session called standing-context; shared and personal counts; no file bootstrap",
                     "provenance": "fresh Codex task readback"},
            "final": {"record_type": "decision", "actor": "codex", "sponsoring_human": "joe",
                      "human_quote": "I approve final doctrine cutoff", "content": "approve cutoff final",
                      "provenance": "log-decision"},
        }
        for kind, data in fixtures.items():
            data["id"] = IDS[kind]
            (self.evidence / f"{IDS[kind]}.json").write_text(json.dumps(data))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args, ok=True, env=None):
        result = subprocess.run([str(PY), str(CUTOFF), "--repo", str(REPO),
                                 "--vault", str(self.vault), *args],
                                env=env or self.env, capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stderr or result.stdout)
        if not ok and result.returncode == 0:
            self.fail("expected refusal")
        return result

    def stage(self, env=None):
        result = self.call("stage", "--approved-commit", "a" * 40,
                           "--monday-evidence", IDS["monday"],
                           "--bootstrap-revision", IDS["bootstrap_workspace"],
                           "--bootstrap-revision", IDS["bootstrap_control"],
                           "--bootstrap-revision", IDS["bootstrap_mature"],
                           "--stage-approval", IDS["stage"],
                           "--rollback-approval", IDS["rollback_approval"], env=env)
        body = json.loads(result.stdout)
        self.stage_evidence = body["stage_evidence_id"]
        return Path(body["stage"])

    def test_stage_finalize_rollback_round_trip(self):
        stage = self.stage()
        self.assertFalse((self.vault / "CLAUDE.md").exists())
        self.assertFalse(self.launch_state.exists())
        config = json.loads(self.config.read_text())
        self.assertTrue(config["doctrine.md_renders_retiring"]["value"])
        manifest = json.loads((stage / "manifest.json").read_text())
        self.assertEqual(manifest["file_count"], len(manifest["files"]))
        self.call("finalize", "--stage", str(stage),
                  "--cold-start-evidence", IDS["cold"],
                  "--stage-evidence", self.stage_evidence,
                  "--final-approval", IDS["final"])
        self.assertEqual(json.loads(self.sentinel.read_text())["phase"], "finalized")
        self.call("rollback", "--stage", str(stage))
        self.assertTrue((self.vault / "CLAUDE.md").exists())
        self.assertTrue(self.launch_state.exists())
        self.assertFalse(self.sentinel.exists())

    def test_missing_or_wrong_evidence_refuses_before_move(self):
        result = self.call("stage", "--approved-commit", "a" * 40,
                           "--monday-evidence", "short-id",
                           "--bootstrap-revision", IDS["bootstrap_workspace"],
                           "--bootstrap-revision", IDS["bootstrap_control"],
                           "--bootstrap-revision", IDS["bootstrap_mature"],
                           "--stage-approval", IDS["stage"],
                           "--rollback-approval", IDS["rollback_approval"], ok=False)
        self.assertIn("full durable UUID", result.stderr)
        self.assertTrue((self.vault / "CLAUDE.md").exists())

        result = self.call("stage", "--approved-commit", "a" * 40,
                           "--monday-evidence", "66666666-6666-4666-8666-666666666666",
                           "--bootstrap-revision", IDS["bootstrap_workspace"],
                           "--bootstrap-revision", IDS["bootstrap_control"],
                           "--bootstrap-revision", IDS["bootstrap_mature"],
                           "--stage-approval", IDS["stage"],
                           "--rollback-approval", IDS["rollback_approval"], ok=False)
        self.assertIn("evidence record not found", result.stderr)
        self.assertTrue((self.vault / "CLAUDE.md").exists())

    def test_stage_smoke_failure_rolls_back_completely(self):
        env = {**self.env, "CARR_CUTOFF_SMOKE_COMMANDS": json.dumps([["/usr/bin/false"]])}
        self.call("stage", "--approved-commit", "a" * 40,
                  "--monday-evidence", IDS["monday"],
                  "--bootstrap-revision", IDS["bootstrap_workspace"],
                  "--bootstrap-revision", IDS["bootstrap_control"],
                  "--bootstrap-revision", IDS["bootstrap_mature"],
                  "--stage-approval", IDS["stage"],
                  "--rollback-approval", IDS["rollback_approval"], ok=False, env=env)
        self.assertTrue((self.vault / "CLAUDE.md").exists())
        self.assertFalse(self.sentinel.exists())
        self.assertTrue(self.launch_state.exists())
        data = json.loads(self.config.read_text())
        self.assertFalse(data["doctrine.md_renders_retiring"]["value"])

    def test_launchctl_unload_failure_rolls_back_stage_state(self):
        broken = self.root / "broken-launchctl"
        broken.write_text("#!/bin/sh\nexit 1\n")
        broken.chmod(0o755)
        env = {**self.env, "CARR_CUTOFF_LAUNCHCTL": str(broken)}
        self.call("stage", "--approved-commit", "a" * 40,
                  "--monday-evidence", IDS["monday"],
                  "--bootstrap-revision", IDS["bootstrap_workspace"],
                  "--bootstrap-revision", IDS["bootstrap_control"],
                  "--bootstrap-revision", IDS["bootstrap_mature"],
                  "--stage-approval", IDS["stage"],
                  "--rollback-approval", IDS["rollback_approval"], ok=False, env=env)
        self.assertTrue((self.vault / "CLAUDE.md").exists())
        self.assertFalse(self.sentinel.exists())
        self.assertFalse(self.config.exists())

    def test_rollback_collision_moves_nothing(self):
        stage = self.stage()
        collision = self.vault / self.render_paths[0]
        collision.parent.mkdir(parents=True, exist_ok=True); collision.write_text("collision")
        another = self.render_paths[1]
        self.call("rollback", "--stage", str(stage), ok=False)
        self.assertEqual(collision.read_text(), "collision")
        self.assertTrue((stage / another).exists())
        data = json.loads(self.config.read_text())
        self.assertTrue(data["doctrine.md_renders_retiring"]["value"])

    def test_interrupted_rollback_resumes_from_verified_destination(self):
        stage = self.stage()
        rel = self.render_paths[0]
        source, destination = stage / rel, self.vault / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        self.call("rollback", "--stage", str(stage))
        self.assertTrue(destination.exists())
        self.assertTrue((self.vault / "CLAUDE.md").exists())
        self.assertFalse(self.sentinel.exists())

    def test_manifest_traversal_refuses(self):
        stage = self.stage()
        manifest_path = stage / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][0]["path"] = "../escape"
        manifest_path.write_text(json.dumps(manifest))
        self.call("rollback", "--stage", str(stage), ok=False)
        self.assertFalse((self.root / "escape").exists())

    def test_tampered_stage_restores_actual_bytes_and_records_integrity_alarm(self):
        stage = self.stage()
        tampered = stage / self.render_paths[0]
        tampered.write_text("tampered")
        self.call("rollback", "--stage", str(stage))
        self.assertEqual((self.vault / self.render_paths[0]).read_text(), "tampered")
        self.assertFalse(self.sentinel.exists())
        self.assertFalse(json.loads(self.config.read_text())[
            "doctrine.md_renders_retiring"]["value"])
        evidence = [json.loads(path.read_text()) for path in self.evidence.glob("*.json")]
        rollback = [row for row in evidence if "integrity_alarm" in row.get("content", "")]
        self.assertTrue(rollback)

    def test_mutation_between_manifest_and_move_restores_every_source_and_flags(self):
        target = self.vault / self.render_paths[0]
        mutator = self.root / "mutate"
        mutator.write_text("#!/bin/sh\nprintf 'mutated during stage\\n' > \"$1\"\n")
        mutator.chmod(0o755)
        env = {**self.env, "CARR_CUTOFF_TEST_BEFORE_MOVE":
               json.dumps([[str(mutator), str(target)]][0])}
        self.call("stage", "--approved-commit", "a" * 40,
                  "--monday-evidence", IDS["monday"],
                  "--bootstrap-revision", IDS["bootstrap_workspace"],
                  "--bootstrap-revision", IDS["bootstrap_control"],
                  "--bootstrap-revision", IDS["bootstrap_mature"],
                  "--stage-approval", IDS["stage"],
                  "--rollback-approval", IDS["rollback_approval"], ok=False, env=env)
        self.assertEqual(target.read_text(), "mutated during stage\n")
        for rel in self.render_paths + ["CLAUDE.md", "AGENTS.md", "00_Context/today.md"]:
            self.assertTrue((self.vault / rel).exists(), rel)
        self.assertFalse(self.sentinel.exists())
        self.assertTrue(self.launch_state.exists())
        config = json.loads(self.config.read_text())
        self.assertFalse(config["doctrine.md_renders_retiring"]["value"])
        self.assertFalse(config["doctrine.md_renders_retired"]["value"])

    def test_finalize_rejects_wrong_evidence_without_changing_stage(self):
        stage = self.stage()
        self.call("finalize", "--stage", str(stage),
                  "--cold-start-evidence", IDS["stage"],
                  "--stage-evidence", self.stage_evidence,
                  "--final-approval", IDS["final"], ok=False)
        self.assertEqual(json.loads(self.sentinel.read_text())["phase"], "staged")
        config = json.loads(self.config.read_text())
        self.assertTrue(config["doctrine.md_renders_retiring"]["value"])
        self.assertFalse(config["doctrine.md_renders_retired"]["value"])

    def test_local_stage_sentinel_keeps_exporter_disabled(self):
        from lib.doctrine_cutoff_state import markdown_writes_blocked
        from exporters.run_exports import md_renders_disabled
        self.sentinel.write_text(json.dumps({"phase": "staged"}))
        old = os.environ.get("CARR_CUTOFF_SENTINEL")
        os.environ["CARR_CUTOFF_SENTINEL"] = str(self.sentinel)
        try:
            self.assertTrue(markdown_writes_blocked(REPO))
            self.assertTrue(md_renders_disabled())
            sys.path.insert(0, str(REPO / "hooks"))
            from md_manifest import md_write_verdict, CUTOFF
            self.assertIsNotNone(md_write_verdict("CLAUDE.md", today=CUTOFF))
        finally:
            if old is None: os.environ.pop("CARR_CUTOFF_SENTINEL", None)
            else: os.environ["CARR_CUTOFF_SENTINEL"] = old

    def test_deployed_consumer_refuses_before_state_change(self):
        front = self.vault / "DNA/Team/front-door.html"
        front.parent.mkdir(parents=True, exist_ok=True)
        front.write_text("read 00_Context/today.md")
        self.call("stage", "--approved-commit", "a" * 40,
                  "--monday-evidence", IDS["monday"],
                  "--bootstrap-revision", IDS["bootstrap_workspace"],
                  "--bootstrap-revision", IDS["bootstrap_control"],
                  "--bootstrap-revision", IDS["bootstrap_mature"],
                  "--stage-approval", IDS["stage"],
                  "--rollback-approval", IDS["rollback_approval"], ok=False)
        self.assertFalse(self.config.exists())
        self.assertTrue((self.vault / "CLAUDE.md").exists())

    def test_launchd_pointer_mismatch_refuses_before_state_change(self):
        plist = self.fake_home / "Library/LaunchAgents/com.carr.rules-refresh.plist"
        with plist.open("wb") as fh:
            plistlib.dump({"Label": "com.carr.rules-refresh",
                           "ProgramArguments": ["/bin/zsh", "/wrong/refresh-rules.sh"]}, fh)
        self.call("stage", "--approved-commit", "a" * 40,
                  "--monday-evidence", IDS["monday"],
                  "--bootstrap-revision", IDS["bootstrap_workspace"],
                  "--bootstrap-revision", IDS["bootstrap_control"],
                  "--bootstrap-revision", IDS["bootstrap_mature"],
                  "--stage-approval", IDS["stage"],
                  "--rollback-approval", IDS["rollback_approval"], ok=False)
        self.assertFalse(self.config.exists())

    def test_stale_installed_managed_skill_refuses_before_state_change(self):
        skill = self.fake_home / ".claude/skills/catchup/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("read open-loops.md in CARR\n")
        self.call("stage", "--approved-commit", "a" * 40,
                  "--monday-evidence", IDS["monday"],
                  "--bootstrap-revision", IDS["bootstrap_workspace"],
                  "--bootstrap-revision", IDS["bootstrap_control"],
                  "--bootstrap-revision", IDS["bootstrap_mature"],
                  "--stage-approval", IDS["stage"],
                  "--rollback-approval", IDS["rollback_approval"], ok=False)
        self.assertFalse(self.config.exists())
        self.assertTrue((self.vault / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
