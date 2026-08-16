#!/usr/bin/env python3
"""Unit checks for the calendar new-attendee completion gate."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "calendar-intake-gate.py"
spec = importlib.util.spec_from_file_location("calendar_intake_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ok   " if condition else "  FAIL ") + name + (f" {detail}" if detail else ""))
    if not condition:
        failed.append(name)


proposals = {"unknown": [{"email": "new@example.com"}]}
complete = {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": "local Mail search: new@example.com"},
    "research": {"status": "searched", "source": "https://example.com/team"},
    "record": {"status": "created", "ref": "V-CPA-999"},
}}}

print("calendar intake gate")
check("complete three-receipt intake passes", gate.unresolved(proposals, complete) == {})
gaps = gate.unresolved(proposals, {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": "local mail"},
    "research": {"status": "searched", "source": "https://example.com"},
}}})
check("missing canonical record refuses", gaps == {"new@example.com": ["record"]}, repr(gaps))
gaps = gate.unresolved(proposals, {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": ""},
    "research": {"status": "searched", "source": "https://example.com"},
    "record": {"status": "ambiguous", "ref": "P-0001"},
}}})
check("empty mail-search and ambiguous identity both refuse",
      gaps == {"new@example.com": ["mail_search", "record"]}, repr(gaps))
gaps = gate.unresolved(proposals, {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": "local mail"},
    "research": {"status": "searched", "source": "https://example.com"},
    "record": {"status": "ambiguous", "ref": "P-0001"},
}}})
check("ambiguous identity remains pending", gaps == {"new@example.com": ["record"]}, repr(gaps))

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    proposal_path, evidence_path = root / "proposals.json", root / "evidence.json"
    proposal_path.write_text(json.dumps(proposals))
    evidence_path.write_text(json.dumps(complete))
    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path)], text=True, capture_output=True)
    check("CLI accepts complete intake", p.returncode == 0, p.stdout + p.stderr)
    evidence_path.unlink()
    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path)], text=True, capture_output=True)
    check("CLI refuses missing evidence file", p.returncode == 78 and "REFUSE" in p.stderr,
          p.stdout + p.stderr)

print("OK all checks passed" if not failed else "FAIL " + ", ".join(failed))
raise SystemExit(bool(failed))
