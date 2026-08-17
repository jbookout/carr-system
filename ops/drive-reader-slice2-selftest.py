#!/usr/bin/env python3
"""Hermetic contracts for the second Drive-reader repoint slice."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = Path(sys.executable)

passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    passed += 1
    print(f"ok {passed:02d} - {label}")


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                          env=env, timeout=90)


with tempfile.TemporaryDirectory(prefix="drive-reader-slice2-") as td:
    tmp = Path(td)
    now = datetime.now(timezone.utc).isoformat()
    health_fixture = tmp / "health.json"
    health_fixture.write_text(json.dumps({
        "exports": {"registered": ["leads"], "rows": [
            {"target": "leads", "last_ok": now, "latest_status": "ok"}
        ]},
        "jobs": [{"definition_key": "nightly", "state": "succeeded",
                  "mode": "normal", "attempt": 1, "max_attempts": 3,
                  "completion_receipt_count": 1}],
        "errors": [],
    }))
    poisoned = dict(os.environ)
    poisoned["CARR_VAULT"] = "/DO-NOT-READ-THIS-DRIVE"

    p = run(str(PY), "tools/health-check.py", "--section", "exports",
            "--fixture", str(health_fixture), env=poisoned)
    check("normal health reads canonical export receipts", p.returncode == 0 and
          "canonical export_run receipts" in p.stdout, p.stdout + p.stderr)
    check("normal health discards ambient CARR_VAULT",
          "DO-NOT-READ" not in p.stdout + p.stderr, p.stdout + p.stderr)

    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(health_fixture), env=poisoned)
    check("normal health reads durable Control Plane jobs", p.returncode == 0 and
          "durable Control Plane job state" in p.stdout and "no terminal failure" in p.stdout,
          p.stdout + p.stderr)

    p = run(str(PY), "tools/health-check.py", "--recovery")
    check("health recovery refuses a missing reason", p.returncode != 0 and
          "nonblank --reason" in p.stderr, p.stdout + p.stderr)

    from lib.registry import REGISTRY_COLUMNS as REGISTRY_COLS
    registry_fixture = tmp / "registry.json"
    row: dict[str, object] = {key: None for key in REGISTRY_COLS}
    row["Lead ID"] = "L-001"
    row["Contact Name"] = "Fixture Person"
    registry_fixture.write_text(json.dumps([row]))
    p = run(str(PY), "tools/registry-audit.py", "--fixture", str(registry_fixture),
            env=poisoned)
    check("normal registry audit uses canonical row shape", p.returncode == 0 and
          "canonical v_export_leads" in p.stdout, p.stdout + p.stderr)
    check("normal registry audit never resolves ambient Drive",
          "DO-NOT-READ" not in p.stdout + p.stderr, p.stdout + p.stderr)

    vault = tmp / "vault"
    leads = vault / "DNA/Leads"
    leads.mkdir(parents=True)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Registry"
    ws.append(list(REGISTRY_COLS))
    ws.append([row.get(key) for key in REGISTRY_COLS])
    wb.create_sheet("Intake Log")
    wb.save(leads / "lead-registry.xlsx")
    p = run(str(PY), "tools/registry-audit.py", "--recovery", "--reason",
            "local projection drill", "--vault", str(vault))
    check("registry recovery is reasoned and labeled noncanonical", p.returncode == 0 and
          "NONCANONICAL Drive projection" in p.stderr and "local projection drill" in p.stderr,
          p.stdout + p.stderr)

    for script in ("tools/check.sh", "tools/smoke.sh"):
        p = run("zsh", script, "--recovery")
        check(f"{script} recovery refuses a missing reason", p.returncode == 2 and
              "nonblank --reason" in p.stderr, p.stdout + p.stderr)

    p = run(str(PY), "tools/report-card.py", "--validate", "--recovery",
            "--reason", "local projection drill", "--vault", str(vault))
    check("report card recovery is reasoned and labeled", p.returncode == 0 and
          "NONCANONICAL Drive projections" in p.stderr, p.stdout + p.stderr)

health_src = (ROOT / "tools/health-check.py").read_text()
registry_src = (ROOT / "tools/registry-audit.py").read_text()
run_src = (ROOT / "run.sh").read_text()
smoke_src = (ROOT / "tools/smoke.sh").read_text()
check("health emits the exact canonical export query",
      "from export_run group by target" in health_src)
check("health emits the exact Control Plane ledger query",
      "from ops.v_job_control" in health_src and "receipt_count" in health_src)
check("registry emits the v_export_leads read through record_sources",
      'load_leads("", MODE_RECORDS)' in registry_src)
check("normal run.sh wrappers do not inject CARR_VAULT into bounded readers",
      'health)       shift; "$PY" "$REPO/tools/health-check.py" "$@"' in run_src and
      'report-card)  shift; "$PY" "$REPO/tools/report-card.py" "$@"' in run_src and
      'registry_audit(){ shift; "$PY"' in run_src)
check("normal smoke calls canonical retrieval without a Drive environment",
      '"$PY" "$REPO/tools/retrieve.py" -n 1' in smoke_src and
      'CARR_VAULT="$VAULT"' not in smoke_src)

print(f"PASS: drive reader slice 2 ({passed} checks)")
