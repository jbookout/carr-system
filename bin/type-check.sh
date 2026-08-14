#!/bin/sh
# type-check.sh — the type-check tripwire (added 2026-08-06, Joe's go:
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
# stale silently and told sessions legacy noise existed when none did.
# If you add an exemption, say why here; a green run means green everywhere.
# Manual path is THE SAME script (rule a8c55a47): ./bin/type-check.sh
# Risk color GREEN: read-only, writes nothing, sends nothing.
#
# PORTABLE ON PURPOSE, since 2026-08-14, when this became a CI check as well as
# a nightly one. Two things here used to assume Joe's Mac and would have made
# the SAME script behave differently in its two callers — the exact failure the
# one-script rule exists to prevent:
#
#   #!/bin/zsh          ubuntu-latest has no zsh, so the GitHub runner could not
#                       execute this file at all. Nothing below is zsh-specific,
#                       so /bin/sh runs identically in both places.
#   ./.venv/bin/mypy    the runner installs requirements.lock into the system
#                       python and has no .venv, so a hardcoded path resolved to
#                       nothing. mypy==2.3.0 IS in the lock, so it is on PATH
#                       there; prefer the venv locally, fall back to PATH.
#
# Missing mypy EXITS 2 rather than passing quietly: a type check that silently
# did not run is the same lie as a green CI that executed nothing.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 2

if [ -x ./.venv/bin/mypy ]; then
  MYPY=./.venv/bin/mypy
elif command -v mypy >/dev/null 2>&1; then
  MYPY=mypy
else
  echo "type-check.sh: no mypy found (looked for ./.venv/bin/mypy and mypy on PATH)" >&2
  echo "type-check.sh: install it with: pip install -r requirements.lock" >&2
  exit 2
fi

exec "$MYPY" pipelines tools exporters lib generators shared fill-engine bin hooks ops
