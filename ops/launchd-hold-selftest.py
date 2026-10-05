#!/usr/bin/env python3
"""Exercise launchd holds against a stateful launchctl, without live effects."""
import contextlib
import importlib.util
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REAL_RUN = subprocess.run
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("cac_hold", ROOT / "ops/config-as-code.py")
cac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cac)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.hold = self.home / ".config/carr/launchd-hold"
        self.hold.parent.mkdir(parents=True)
        self.label = "com.carr.synthetic-held"
        self.hold.write_text(self.label + " API budget exhausted\n")
        self.dest = self.home / (self.label + ".plist")
        self.body = plistlib.dumps({"Label": self.label, "ProgramArguments": ["/usr/bin/true"],
                                   "StartCalendarInterval": [{"Minute": 0}]}).decode()
        self.dest.write_text(self.body)
        self.loaded, self.disabled, self.calls = True, True, []
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, HOME=str(self.home)).start()
        patch.object(cac, "HOME", str(self.home)).start()
        patch.object(cac.subprocess, "run", side_effect=self.launchctl).start()

    def launchctl(self, argv, **kwargs):
        self.calls.append(argv)
        mode = argv[1]
        rc, out = 0, ""
        if mode == "print":
            rc = 0 if self.loaded else 113
            out = f"path = {self.dest}\n" if self.loaded else "Could not find service"
        elif mode == "print-disabled":
            out = f'"{self.label}" => {str(self.disabled).lower()}\n'
        elif mode in {"unload", "bootout"}:
            self.loaded = False
        elif mode == "disable":
            self.disabled = True
        elif mode in {"load", "bootstrap", "enable", "kickstart"}:
            self.loaded, self.disabled = True, False
        return subprocess.CompletedProcess(argv, rc, out, out if rc else "")

    def test_install_keeps_held_job_disabled_and_unloaded(self):
        cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True)
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertFalse(any(c[1] in {"load", "bootstrap", "enable", "kickstart"} for c in self.calls))

    def test_reinstall_with_kickstart_keeps_hold(self):
        row = {"name": self.dest.name, "dest": str(self.dest), "label": self.label,
               "body": self.body, "previous": self.body}
        cac.reinstall_calendar_agent(row, "launchctl", f"gui/{os.getuid()}", True)
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertFalse(any(c[1] in {"bootstrap", "kickstart"} for c in self.calls))

    def test_check_reports_held_with_reason_age(self):
        patch.object(cac, "carr_plists", return_value=[self.dest.name]).start()
        patch.object(cac, "LAUNCHD_SRC", str(self.home)).start()
        patch.object(cac, "launchd_repo_path", return_value=str(self.dest)).start()
        patch.object(cac, "live_hooks_block", return_value={}).start()
        patch.object(cac, "codex_configuration_state", return_value="absent").start()
        patch.object(cac, "TASKS_SRC", str(self.home / "absent")).start()
        patch.object(cac, "tracked_scheduled_task_paths", return_value={}).start()
        self.assertFalse(any(label.startswith("launchd ") for label, _, _ in cac.pairs()))
        patch.object(cac, "pairs", return_value=[]).start()
        for name in ("hook_scripts_untracked", "secondary_scheduled_task_violations",
                     "refused_launchd_templates", "pending_launchd_reloads"):
            patch.object(cac, name, return_value=[]).start()
        patch.object(cac, "PREREQUISITE_CHECK", return_value=[]).start()
        patch.object(cac.continuity_config, "load", return_value={}).start()
        patch.object(cac.continuity_config, "read_mode", return_value=None).start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cac._cmd_check(), 0)
        self.assertIn("HELD", output.getvalue())
        self.assertIn("API budget exhausted", output.getvalue())
        self.assertIn("age", output.getvalue())
        self.assertNotIn("DRIFT", output.getvalue())

    def test_definition_only_template_cannot_load(self):
        self.hold.write_text("")
        body = self.body.replace("<plist", "<!-- carr-launchd-definition-only: awaiting repair -->\n<plist", 1)
        cac.install_launchd_plist(self.dest.name, str(self.dest), body, False)
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertTrue(any(c[1] == "disable" for c in self.calls))

    def test_watchdog_off_exits_before_config_or_scan(self):
        (self.hold.parent / "job-watchdog.off").touch()
        # This process uses a missing config: reaching any setup or scan is a failure.
        patch.stopall()
        result = REAL_RUN([sys.executable, str(ROOT / "tools/job-watchdog.py"),
                           "--config", str(self.home / "missing.json"), "scan"],
                          env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn("job-watchdog.off", result.stdout)


if __name__ == "__main__":
    unittest.main()
