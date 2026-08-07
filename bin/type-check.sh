#!/bin/zsh
# type-check.sh — the nightly type-check tripwire (added 2026-08-06, Joe's go:
# the Python-native answer to the Rust question, loop #218).
# Runs mypy over the repo's Python with the DELIBERATELY LENIENT mypy.ini:
# it catches shape mistakes in data hand-offs (text where a date should be,
# None where a row was assumed), not truth mistakes — output verification
# (protocol rule 28) remains the real safety net above this.
# 19 legacy files are grandfathered with "# mypy: ignore-errors" headers, each
# carrying its own removal instruction; coverage densifies as sessions touch them.
# Manual path is THE SAME script (rule a8c55a47): ./bin/type-check.sh
# Risk color GREEN: read-only, writes nothing, sends nothing.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 2
exec ./.venv/bin/mypy pipelines tools exporters lib generators shared fill-engine bin hooks ops
