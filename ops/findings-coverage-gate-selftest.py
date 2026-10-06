#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "ops/findings-coverage-gate.py"
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    manifest = root / "findings.json"
    manifest.write_text(json.dumps({"findings": [{"id": "F1", "severity": "major"}, {"id": "F2", "severity": "nit"}]}))
    def run(body, success, args=()):
        brief = root / "brief.txt"
        brief.write_text(body)
        result = subprocess.run([sys.executable, str(GATE), "--root", str(root), "--brief", str(brief), *args], capture_output=True, text=True)
        assert (result.returncode == 0) == success, (body, result.stdout, result.stderr)
    good = "Findings-source: findings.json\nFindings-total: 2\nFinding: F1 | fixed | Regression test passes.\nFinding: F2 | not_a_defect | Spacing is required by the design.\n"
    run(good, True)
    for declaration in ("Findings-source:", "Findings-total: banana", "Findings-total:", "Findings-total: 2", "Findings-total: 2.0"):
        run(good + declaration + "\n", False)
    run(good.replace("Findings-source: findings.json", "Findings-source:"), False)
    run(good.replace("Finding: F2 | not_a_defect | Spacing is required by the design.\n", ""), False)
    run(good.replace("not_a_defect", "deferred"), False)
    run(good.replace("Spacing is required by the design.", ""), False)
    run(good.replace("Findings-total: 2", "Findings-total: 1"), False)
    run(good + "Finding: F2 | fixed | Duplicate.\n", False)
    run(good + "Finding: F3 | fixed | Unknown.\n", False)
    run(good.replace("fixed", "planned"), False)
    run(good.replace("fixed", "planned"), True, ("--phase", "brief"))
    run("No findings in this change.\n", True)
    run(good.replace("findings.json", "../findings.json"), False)
    manifest.write_text(json.dumps({"findings": [{"id": "F1"}, {"id": "F1"}]}))
    run(good, False)
    manifest.write_text(json.dumps({"findings": [{"id": "F1"}, {}]}))
    run(good, False)
    event = root / "event.json"
    event.write_text("{}"); env = dict(os.environ, GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))
    result = subprocess.run([sys.executable, str(GATE)], env=env, capture_output=True, text=True)
    assert result.returncode != 0, "unreadable PR event must fail"

import importlib.util
spec = importlib.util.spec_from_file_location("integrity", ROOT / "hooks/gate-integrity.py")
assert spec and spec.loader
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)
assert integrity.CONTRACTS.get("findings-coverage-gate.py") == str(GATE)
assert integrity.CONTRACTS.get("ci.sh") == str(ROOT / "ops/ci.sh")
print("PASS findings coverage: complete count, all severities, reasons, exact IDs, and PR event refusal")
