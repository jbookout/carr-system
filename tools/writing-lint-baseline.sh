#!/bin/zsh
# Regenerate baselines/writing-lint.txt after an intentional rule change.
# The fixtures are the contract: clean-email.txt must stay 0 HARD (false-positive
# guard), dirty-social.txt must keep catching every planted tell (detection guard).
set -eu
R="$(cd "$(dirname "$0")/.." && pwd)"
{ echo "# writing-lint baseline. Regenerate: tools/writing-lint-baseline.sh"
  echo "## clean-email.txt --surface email  (must stay 0 HARD)"
  python3 "$R/tools/writing-lint.py" "$R/tools/fixtures/clean-email.txt" --surface email | tail -n +2
  echo
  echo "## dirty-social.txt --surface social"
  python3 "$R/tools/writing-lint.py" "$R/tools/fixtures/dirty-social.txt" --surface social | tail -n +2
} > "$R/baselines/writing-lint.txt"
echo "baselines/writing-lint.txt regenerated"
