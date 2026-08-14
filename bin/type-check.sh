#!/bin/zsh
# type-check.sh — the nightly type-check tripwire (added 2026-08-06, Joe's go:
# the Python-native answer to the Rust question, loop #218).
# Runs mypy over the repo's Python with the DELIBERATELY LENIENT mypy.ini:
# it catches shape mistakes in data hand-offs (text where a date should be,
# None where a row was assumed), not truth mistakes — output verification
# (protocol rule 28) remains the real safety net above this.
# THE GRANDFATHER LIST IS EMPTY (verified 2026-08-14). This header used to say
# "19 legacy files are grandfathered with # mypy: ignore-errors headers"; those
# headers were self-removing and every one of them has since been removed, so
# the sweep now covers every file it names with no exemptions at all. Stating
# the CONDITION rather than a count, per rule b01edd26 — the old number went
# stale silently, and it told sessions a legacy-noise bucket existed, which is
# the one place a real regression can be waved off as already known.
# If you add an exemption, say why here; a green run means green everywhere.
# Manual path is THE SAME script (rule a8c55a47): ./bin/type-check.sh
# Risk color GREEN: read-only, writes nothing, sends nothing.
# WHERE mypy COMES FROM, and why this is not just ./.venv/bin/mypy any more.
# Since 2026-08-14 this script is also CI's `typecheck` class (ops/ci.sh), so it
# runs in two environments: Joe's Mac, which has a .venv, and a GitHub runner,
# which pip-installs requirements.lock into the system python and has no .venv
# at all. One script, two homes — rule a8c55a47 says the manual path and the
# automated path must be the SAME CODE, and the directory list below is the
# thing that must not fork.
#
# Exit 78 (EX_CONFIG) when mypy is absent anywhere: the nightly chain reads 78
# as SKIP rather than FAIL, and CI reads it as a skip that --strict then refuses.
# Neither treats "the tool is missing" as "the code is broken".
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 2

MYPY="$REPO/.venv/bin/mypy"
if [ ! -x "$MYPY" ]; then
  MYPY="$(command -v mypy 2>/dev/null || true)"
fi
if [ -z "$MYPY" ] || [ ! -x "$MYPY" ]; then
  echo "type-check: mypy is not installed (it is pinned in requirements.lock)" >&2
  exit 78
fi

exec "$MYPY" pipelines tools exporters lib generators shared fill-engine bin hooks ops
