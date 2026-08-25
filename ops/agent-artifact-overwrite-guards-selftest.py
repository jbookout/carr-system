#!/usr/bin/env python3
"""Keep partial captures and same-name collisions from replacing good artifacts."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
checks = {
    "salesforce reader compares existing and candidate row counts before overwrite": (
        ROOT / "claude-tree/agents/salesforce-reader.md",
        ("read the existing file and count", "new capture has fewer rows, do not write", "partial capture never replaces"),
    ),
    "client intake requires identity beyond a same-name path and merges continuations": (
        ROOT / "claude-tree/agents/client-intake.md",
        ("same display name is not identity evidence", "resolve the record to the same C-ID", "ordinary continuation", "do not merge two people"),
    ),
    "benefit summary reconciles terms and preserves an existing post-mortem": (
        ROOT / "claude-tree/agents/benefit-summary.md",
        ("reconcile every existing deal-point row", "complete capture with a partial one", "edit it in place", "never replace the whole post-mortem"),
    ),
}

failed: list[str] = []
for label, (path, required) in checks.items():
    text = " ".join(path.read_text(encoding="utf-8").split())
    missing = [phrase for phrase in required if phrase not in text]
    print(("  ok  " if not missing else "  FAIL  ") + label)
    if missing:
        failed.append(f"{label}: {missing}")

if failed:
    raise SystemExit("FAIL: " + "; ".join(failed))
print("PASS: agent artifact overwrite guards")
