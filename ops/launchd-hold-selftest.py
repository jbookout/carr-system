#!/usr/bin/env python3
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
assert spec is not None and spec.loader is not None
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
            out = f"path = {self.dest}\n" if self.loaded else f'Could not find service "{self.label}"'
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
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "held")
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

    def test_hold_added_during_bootout_prevents_bootstrap(self):
        self.hold.write_text("")
        def late_hold(argv, **kwargs):
            result = self.launchctl(argv, **kwargs)
            if argv[1] == "bootout":
                self.hold.write_text(self.label + " operator stopped it during reload\n")
            return result
        cac.subprocess.run.side_effect = late_hold
        row = {"name": self.dest.name, "dest": str(self.dest), "label": self.label,
               "body": self.body, "previous": self.body}
        self.assertTrue(cac.reinstall_calendar_agent(row, "launchctl", f"gui/{os.getuid()}", True))
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertFalse(any(c[1] in {"bootstrap", "kickstart"} for c in self.calls))

    def test_unheld_matching_job_remains_loaded(self):
        self.hold.write_text("")
        self.disabled = False
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "kept")
        self.assertTrue(self.loaded)
        self.assertFalse(self.disabled)
        self.assertEqual([c[1] for c in self.calls], ["print"])

    def test_disabled_readback_accepts_words_and_rejects_another_label(self):
        for key, expected in ((self.label, "held"), ("other" + self.label, "failed")):
            self.calls.clear()
            def words(argv, **kwargs):
                result = self.launchctl(argv, **kwargs)
                if argv[1] == "print-disabled":
                    result.stdout = f'{key} => disabled\n'
                return result
            cac.subprocess.run.side_effect = words
            self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), expected)

    def test_invalid_hold_file_refuses_without_launchctl(self):
        self.hold.write_text(self.label + "\n")
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "failed")
        self.assertEqual(self.calls, [])

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
        patch.object(cac, "git_hooks_path_report", return_value="fixture hooks").start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cac.cmd_check(), 0)
        self.assertTrue(output.getvalue().splitlines()[0].startswith("config-as-code: OK"))
        self.assertIn("HELD", output.getvalue())
        self.assertIn("API budget exhausted", output.getvalue())
        self.assertIn("age", output.getvalue())
        self.assertNotIn("DRIFT", output.getvalue())

    def test_definition_only_template_cannot_load(self):
        self.hold.write_text("")
        body = self.body.replace("<plist", "<!-- carr-launchd-definition-only: awaiting repair -->\n<plist", 1)
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), body, False), "held")
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertTrue(any(c[1] == "disable" for c in self.calls))

    def test_watchdog_off_exits_before_config_or_scan(self):
        (self.hold.parent / "job-watchdog.off").touch()
        patch.stopall()
        result = REAL_RUN([sys.executable, str(ROOT / "tools/job-watchdog.py"),
                           "--config", str(self.home / "missing.json"), "scan"],
                          env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn("job-watchdog.off", result.stdout)

    def test_fleet_sync_logs_hold_and_does_not_enable(self):
        patch.stopall()
        fleet_spec = importlib.util.spec_from_file_location("fleet_hold_fixture", ROOT / "ops/fleet-sync-selftest.py")
        fleet = importlib.util.module_from_spec(fleet_spec)
        fleet_spec.loader.exec_module(fleet)
        clone = fleet.build(str(self.home))
        fake_dir = self.home / "stubs"
        fake_dir.mkdir()
        state = self.home / "state.json"
        state.write_text('{"loaded":true,"disabled":true,"calls":[]}')
        fake = fake_dir / "launchctl"
        fake.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
p=Path({str(state)!r}); s=json.loads(p.read_text()); mode=sys.argv[1]
s['calls'].append(sys.argv[1:]); rc=0
if mode=='print':
    if sys.argv[-1].endswith('com.carr.fleet-sync'): rc=113
    elif s['loaded']: print('path = '+{str(self.dest)!r})
    else: print('Could not find service "{self.label}"', file=sys.stderr); rc=113
elif mode=='print-disabled': print('"{self.label}" => '+str(s['disabled']).lower())
elif mode=='disable': s['disabled']=True
elif mode in ('unload','bootout'): s['loaded']=False
else: s['disabled']=False; s['loaded']=True
p.write_text(json.dumps(s)); sys.exit(rc)
''')
        fake.chmod(0o755)
        installer = Path(clone) / "ops/config-as-code.py"
        installer.write_text(f'''import importlib.util, sys
s=importlib.util.spec_from_file_location('real_cac',{str(ROOT / 'ops/config-as-code.py')!r})
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
if sys.argv[1]=='launchd-held': sys.exit(m.report_launchd_holds('fleet-sync: '))
sys.exit(0 if m.install_launchd_plist({self.dest.name!r},{str(self.dest)!r},{self.body!r},True)=='held' else 1)
''')
        if os.environ.get("CARR_TEST_HOLD_BASELINE") == "1":
            old = REAL_RUN(["git", "show", "HEAD:bin/fleet-sync.sh"], cwd=ROOT,
                           env=fleet.ENV, capture_output=True, text=True, check=True).stdout
            (Path(clone) / "bin/fleet-sync.sh").write_text(old)
        result = fleet.run_sync(clone, {"HOME": str(self.home), "PATH": str(fake_dir)+os.pathsep+os.environ["PATH"]})
        import json
        actual = json.loads(state.read_text())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fleet-sync: HELD " + self.label, result.stdout)
        self.assertTrue(actual["disabled"])
        self.assertFalse(actual["loaded"])
        self.assertFalse(any(c[0] in {"enable", "load", "bootstrap", "kickstart"} for c in actual["calls"]))

        staged = self.home / "staged.plist"
        staged.write_text(self.body)
        import hashlib
        for reason in ("local hold", "definition only"):
            state.write_text('{"loaded":true,"disabled":false,"calls":[]}')
            if reason == "definition only":
                self.hold.write_text("")
                self.dest.write_text(self.body.replace("<plist", "<!-- carr-launchd-definition-only: repair -->\n<plist", 1))
            previous_hash = hashlib.sha256(self.dest.read_bytes()).hexdigest()
            gone = subprocess.Popen(["/usr/bin/true"])
            gone.wait()
            result = REAL_RUN(["/bin/bash", "-c", cac.SELF_RELOAD_SCRIPT, "carr-self-reload",
                               str(gone.pid), str(fake), f"gui/{os.getuid()}", self.label,
                               str(staged), str(self.dest), str(self.home / "handoff.log"), "3",
                               previous_hash, sys.executable, str(ROOT / "lib/launchd_hold.py"), str(self.dest)],
                              env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True, timeout=10)
            actual = json.loads(state.read_text())
            self.assertEqual(result.returncode, 0, (self.home / "handoff.log").read_text())
            self.assertTrue(actual["disabled"])
            self.assertFalse(actual["loaded"])
            self.assertFalse(any(c[0] == "bootstrap" for c in actual["calls"]))


if __name__ == "__main__":
    unittest.main()
