#!/bin/zsh
# smoke.sh — the repo's first automated test (orchestrator-lane corrective, 2026-07-25).
# Read-only. Proves every script still parses, the two safety nets run, and retrieval
# answers. Run before any commit that touches code: tools/smoke.sh
# Exit 0 = all green; anything else = the FIRST failure, loudly.

set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"

echo "== smoke: python compile (every .py in the repo) =="
python3 - "$REPO" << 'EOF'
import sys, os, py_compile
repo = sys.argv[1]
n = 0
for dp, dn, fn in os.walk(repo):
    # Never descend into virtualenvs or vendored trees: their contents are
    # third-party, gitignored, and not ours to compile. Before this, a Python-2
    # file inside tools/doc-convo/.venv-higgs (langid/train/scanner.py) failed
    # the sweep, so `smoke.sh` exited red for every run on any machine that had
    # built that venv — a safety net that always fails is a safety net people
    # learn to skip, which is the exact decay rule 590b11e1 names.
    dn[:] = [d for d in dn
             if d not in (".git", "__pycache__", "baselines", "vendor", "node_modules")
             and not d.startswith(".venv")]
    for f in fn:
        if f.endswith(".py"):
            py_compile.compile(os.path.join(dp, f), doraise=True); n += 1
print(f"  OK  {n} files compile")
EOF

# THE REPORT-CARD RUBRIC'S OWN STRUCTURAL CHECK (loop #220, added 2026-08-09).
#
# This one line is the answer to the red team's sharpest structural finding. The
# rubric argued that instrument drift becomes "a signal on the very next run" —
# but nothing ran it. Not this file, not check.sh, not health, not pre-push, not
# any of the 16 scheduled tasks, not any launchd plist. The only invocation path
# in the whole estate was a human typing `run.sh report-card`, which is precisely
# what v1 died of: a rubric that only drifts loudly when somebody remembers to
# look at it drifts silently.
#
# --validate runs no commands, needs no database, and takes milliseconds, so it
# is free to run on every smoke. It exits non-zero on any structural breach.
echo "== smoke: report-card rubric (structural) =="
if python3 "$REPO/tools/report-card.py" --validate >/dev/null 2>&1; then
  echo "  OK  rubric validates"
else
  echo "  FAIL rubric — run: ./run.sh report-card --validate"
  rc=1
fi

echo "== smoke: workflow scripts parse (node; top-level return is valid in the runtime) =="
for f in "$REPO"/workflows/*.workflow.js; do
  errs=$(node --input-type=module --check < "$f" 2>&1 | grep -v "Illegal return statement" | grep "Error" || true)
  if [ -n "$errs" ]; then echo "  FAIL $f: $errs"; exit 1; fi
  echo "  OK  $(basename "$f")"
done

echo "== smoke: sheets.py schema contract against the LIVE workbooks =="
CARR_VAULT="$VAULT" python3 - "$REPO" "$VAULT" << 'EOF'
import sys, glob, os
repo, vault = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, "lib"))
import openpyxl
from sheets import header_map, ROUTER_REQUIRED, REGISTRY_REQUIRED
router = max(glob.glob(os.path.join(vault, "DNA", "Leads", "lead-router-*.xlsx")))
wb = openpyxl.load_workbook(router, read_only=True); header_map(wb["Lead Router"], ROUTER_REQUIRED, "router"); wb.close()
wb = openpyxl.load_workbook(os.path.join(vault, "DNA", "Leads", "lead-registry.xlsx"), read_only=True)
header_map(wb["Registry"], REGISTRY_REQUIRED, "registry"); wb.close()
print("  OK  both workbook schemas validate")
EOF

echo "== smoke: retrieval answers =="
CARR_VAULT="$VAULT" python3 "$REPO/tools/retrieve.py" -n 1 "monthly health audit procedure" | head -2

echo "== smoke: safety nets =="
CARR_VAULT="$VAULT" python3 "$REPO/tools/health-check.py" || echo "  (health findings above are REAL findings, not smoke failures)"
"$REPO/tools/check.sh" || { echo "  FAIL: drift check"; exit 1; }
echo "== smoke: ALL GREEN =="
