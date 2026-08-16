#!/usr/bin/env python3
"""Refuse a release performance claim that lacks approved, bounded evidence.

The caller measures elapsed time; this gate never invents a measurement, a
recovery receipt, or an approval.  It only validates the supplied evidence and
returns non-zero when the approved budget is exceeded.
"""

from __future__ import annotations

import argparse
import re
import sys

REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{2,200}$")


def valid_ref(value: str) -> bool:
    return bool(REF.fullmatch(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elapsed-ms", required=True, type=int)
    parser.add_argument("--budget-ms", required=True, type=int)
    parser.add_argument("--budget-ref", required=True)
    parser.add_argument("--evidence-ref", required=True)
    args = parser.parse_args()
    if args.elapsed_ms <= 0 or args.budget_ms <= 0:
        print("performance-budget-gate: elapsed and budget must both be > 0",
              file=sys.stderr)
        return 2
    if not valid_ref(args.budget_ref) or not valid_ref(args.evidence_ref):
        print("performance-budget-gate: budget and evidence references must be stable non-empty refs", file=sys.stderr)
        return 2
    if args.elapsed_ms > args.budget_ms:
        print(f"performance-budget-gate: FAIL {args.elapsed_ms}ms exceeds approved "
              f"{args.budget_ms}ms ({args.budget_ref}); evidence={args.evidence_ref}", file=sys.stderr)
        return 1
    print(f"performance-budget-gate: PASS {args.elapsed_ms}ms <= {args.budget_ms}ms "
          f"({args.budget_ref}); evidence={args.evidence_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
