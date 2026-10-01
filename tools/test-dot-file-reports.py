#!/usr/bin/env python3
"""Synthetic behavioral verification; never reads live Dot files or credentials."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_VERB = '''#!/usr/bin/env python3
import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
assert sys.argv[1:] == ["call", "record-finding", "-"]
payload = json.load(sys.stdin)
with (root / "requests.jsonl").open("a") as out:
    out.write(json.dumps(payload) + "\\n")
time.sleep(0.05)
if (root / "fail").exists():
    print("synthetic-private-error", file=sys.stderr)
    sys.exit(1)
if (root / "malformed").exists():
    print("{bad synthetic-private-error")
    sys.exit(0)
print(json.dumps({"ok": not (root / "wrong-ack").exists(), "flag_id": "00000000-0000-4000-8000-000000000001",
                  "subject_type": "repo", "kind": "research_report", "found": True}))
'''


class FilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        shutil.copy2(ROOT / "bin/dot-file-reports", self.root / "bin/dot-file-reports")
        (self.root / "run.sh").write_text(FAKE_VERB)
        (self.root / "run.sh").chmod(0o755)
        self.reports = self.root / "out/orch/dot/reports"
        self.reports.mkdir(parents=True)
        self.ledger = self.reports.parent / "filed-reports.json"

    def report(self, job="010-V-synthetic", text="# Synthetic topic\nSee https://example.org/source.\n"):
        path = self.reports / (job + ".txt")
        path.write_bytes(text.encode("utf-8"))
        os.utime(path, (1767225600, 1767225600))  # fixture: 2026-01-01 UTC
        return path

    def command(self, *args):
        return [sys.executable, str(self.root / "bin/dot-file-reports"), *map(str, args)]

    def run_filer(self, *args, ok=True):
        proc = subprocess.run(self.command(*args), capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0 if ok else 1, proc.stderr)
        self.assertNotIn("synthetic-private-error", proc.stdout + proc.stderr)
        return proc

    def requests(self):
        path = self.root / "requests.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def entries(self):
        return json.loads(self.ledger.read_text())["jobs"]

    def test_full_multi_page_report_and_metadata(self):
        text = "# Synthetic metro research\r\n" + "Unicode: café — λ\r\n" * 15000
        text += "[Source](https://example.org/a)\r\nhttps://example.org/a\r\nhttps://example.net/b.\r\n"
        path = self.report("020-M-synthetic", text)
        self.run_filer()
        request, = self.requests()
        self.assertGreater(len(text.encode("utf-8")), 200000)
        self.assertEqual(request["value"]["text"], text)
        self.assertEqual(request["value"]["job"], "020-M-synthetic")
        self.assertEqual(request["value"]["topic"], "Synthetic metro research")
        self.assertEqual(request["value"]["date"], "2026-01-01")
        self.assertEqual(request["value"]["citations"], ["https://example.org/a", "https://example.net/b"])
        self.assertEqual(request["source"], str(path.resolve()))
        self.assertEqual(request["subject"], "repo:jbookout/doctorcre-app")
        self.assertEqual(request["epistemic_status"], "observed")
        self.assertTrue(request["internal"])
        self.assertFalse(request["value"]["claims_independently_verified"])
        self.assertEqual(self.entries()[path.stem]["status"], "filed")
        self.assertNotIn("text", self.ledger.read_text())
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)

    def test_repeat_job_never_refiles_even_when_edited(self):
        path = self.report()
        self.run_filer(path)
        path.write_text("changed synthetic report")
        self.run_filer(path)
        self.assertEqual(len(self.requests()), 1)

    def test_only_selected_file_and_partial_exclusion(self):
        selected = self.report("030-UX-synthetic")
        self.report("040-synthetic")
        partial = self.reports / "050-synthetic.txt.partial"
        partial.write_text("unfinished")
        self.report("060-synthetic.partial")
        self.run_filer(selected)
        self.assertEqual(list(self.entries()), [selected.stem])
        self.run_filer(partial)
        self.run_filer()
        self.assertEqual(len(self.requests()), 2)

    def test_concurrent_runs_file_once(self):
        self.report()
        procs = [subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(3)]
        for proc in procs:
            _, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(len(self.requests()), 1)

    def test_ambiguous_failure_reuses_exact_frozen_request(self):
        path = self.report()
        failure = self.root / "fail"
        failure.touch()
        self.run_filer(ok=False)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")
        failure.rename(self.root / "_to_delete-fail")
        os.utime(path, None)  # metadata changes must not change replay arguments
        self.run_filer()
        first, second = self.requests()
        self.assertEqual(first, second)
        self.assertEqual(self.entries()[path.stem]["status"], "filed")

    def test_changed_pending_content_refuses_replay(self):
        path = self.report()
        (self.root / "fail").touch()
        self.run_filer(ok=False)
        path.write_text("different synthetic content")
        self.run_filer(ok=False)
        self.assertEqual(len(self.requests()), 1)

    def test_malformed_acknowledgement_keeps_pending(self):
        path = self.report()
        (self.root / "malformed").touch()
        self.run_filer(ok=False)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")

    def test_rejected_receipt_under_optimized_python_keeps_pending(self):
        path = self.report()
        (self.root / "wrong-ack").touch()
        command = self.command()
        command.insert(1, "-O")
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")

    def test_corrupt_ledger_refuses_without_call(self):
        self.report()
        self.ledger.write_text("{broken")
        self.run_filer(ok=False)
        self.assertEqual(self.requests(), [])

    def test_empty_invalid_utf8_missing_and_symlink_refuse(self):
        path = self.report(text=" ")
        self.run_filer(path, ok=False)
        path.write_bytes(b"\xff")
        self.run_filer(path, ok=False)
        self.run_filer(self.reports / "missing.txt", ok=False)
        link = self.reports / "link.txt"
        link.symlink_to(path)
        self.run_filer(link, ok=False)
        self.assertEqual(self.requests(), [])


class StdinTransportTests(unittest.TestCase):
    def test_sanctioned_wrapper_to_node_preserves_large_stdin_and_argv_baseline(self):
        # Synthetic Node preload observes the existing client at the fetch seam,
        # before auth, network or DB. No token fixture or secret value is required.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tools").mkdir()
            (root / "mcp-server").mkdir()
            shutil.copy2(ROOT / "tools/call-verb.py", root / "tools/call-verb.py")
            for name in ("local-verb.mjs", "local-client-auth.mjs", "human-only-hint.mjs"):
                shutil.copy2(ROOT / "mcp-server" / name, root / "mcp-server" / name)
            # Load source up to its parsed args; zero authority side effects.
            client = root / "mcp-server/local-verb.mjs"
            source = client.read_text()
            boundary = '// ---------------------------------------------------------------------------\n// DEFAULT PATH'
            self.assertIn(boundary, source)
            client.write_text(source.split(boundary)[0] + '\nconsole.log(JSON.stringify(args));\n')
            env = {key: val for key, val in os.environ.items()
                   if key not in ("DATABASE_URL", "CARR_BREAK_GLASS", "CARR_MCP_CLIENT_PROFILE")}
            payload = {"value": {"text": "synthetic λ\r\n" * 25000}}
            proc = subprocess.run([sys.executable, str(root / "tools/call-verb.py"), "record-finding", "-"],
                                  input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), payload)
            proc = subprocess.run([sys.executable, str(root / "tools/call-verb.py"), "standing-context", "{}"],
                                  text=True, capture_output=True, env=env, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {})


if __name__ == "__main__":
    unittest.main()
