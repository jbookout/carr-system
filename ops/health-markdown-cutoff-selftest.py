#!/usr/bin/env python3
"""The markdown export cutoff scopes the dead-man watch by registry paths.

The health script is intentionally executable at module import, so this test
extracts only its pure filtering function. The fixtures distinguish retired
export targets from still-live Markdown-producing jobs; a suffix-only filter
would incorrectly retire the latter.
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "health-check.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
function = next(
    (node for node in tree.body
     if isinstance(node, ast.FunctionDef) and node.name == "_retired_watch_entries"),
    None,
)
if function is None:
    sys.exit("health-markdown-cutoff-selftest: filter function is missing")
namespace: dict[str, object] = {}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
WatchEntry = tuple[str, str]
FilterWatch = Callable[
    [list[WatchEntry], set[str]],
    tuple[list[WatchEntry], list[WatchEntry]],
]
filter_watch = cast(FilterWatch, namespace["_retired_watch_entries"])


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"ok - {label}")


# These are the four current WATCH entries whose exact paths are exporter
# targets and therefore stop being written at the cutoff.
retired_paths = {
    "DNA/Clients/clients-active.md",
    "DNA/compiled-rules-shared.md",
    "00_Context/compiled-rules-joe.md",
    "DNA/Network/introduction-rules.md",
}
watch = [
    ("GEN clients-active", "DNA/Clients/clients-active.md"),
    ("GEN rules shared", "DNA/compiled-rules-shared.md"),
    ("GEN rules joe", "00_Context/compiled-rules-joe.md"),
    ("GEN rules intro", "DNA/Network/introduction-rules.md"),
    # These remain live jobs despite producing Markdown.
    ("Radar digest", "Automation/radar/radar-digest-*.md"),
    ("JOB matcher", "/Users/booko/carr-system/out/availability-matches.md"),
    ("JOB cadence", "/Users/booko/carr-system/out/cadence-latest.md"),
    ("JOB review-queue", "/Users/booko/carr-system/out/review-queue/review-queue.html"),
]
active, retired = filter_watch(watch, retired_paths)
check("retired exporter rows leave the dead-man watch",
      [entry[0] for entry in retired] == [
          "GEN clients-active", "GEN rules shared", "GEN rules joe", "GEN rules intro"])
check("still-live Markdown jobs remain watched",
      {entry[0] for entry in active} >= {"Radar digest", "JOB matcher", "JOB cadence"})
check("non-Markdown live producer remains watched",
      "JOB review-queue" in {entry[0] for entry in active})
check("a disabled cutoff preserves every watch row",
      filter_watch(watch, set()) == (watch, []))

source = SOURCE.read_text(encoding="utf-8")
check("health derives retirement from the shared cutoff flag",
      "from exporters.run_exports import md_renders_retired as _md_retired" in source)
check("health derives retirement from exporter target paths",
      "from exporters.targets import TARGETS as _EXPORT_TARGETS" in source and
      "rel.lower().endswith(\".md\")" in source)
check("health applies exact-path filtering rather than suffix filtering",
      "_retired_watch_entries(WATCH, _retired_paths)" in source)

print("health-markdown-cutoff-selftest: passed")
