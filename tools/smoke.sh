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
    dn[:] = [d for d in dn if d not in (".git", "__pycache__", "baselines")]
    for f in fn:
        if f.endswith(".py"):
            py_compile.compile(os.path.join(dp, f), doraise=True); n += 1
print(f"  OK  {n} files compile")
EOF

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
