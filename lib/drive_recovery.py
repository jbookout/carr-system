"""The explicit, noncanonical Drive boundary for legacy local artifacts.

Normal callers must never obtain a Drive path from this module.  A caller that
still needs a legacy projection has to ask for recovery, say why, and label its
output.  This is deliberately a boundary, not a canonical-source adapter.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


DEFAULT_RECOVERY_VAULT = Path(
    "/Users/booko/Library/CloudStorage/"
    "GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"
)


def add_recovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recovery", action="store_true",
                        help="use noncanonical Drive projections during an acknowledged outage")
    parser.add_argument("--reason", help="nonblank reason for the recovery exercise")
    parser.add_argument("--vault", help="Drive recovery root; requires --recovery")


def require_recovery(args: argparse.Namespace, seam: str) -> Path:
    """Return a Drive root only after explicit, reasoned recovery selection."""
    if args.vault and not args.recovery:
        raise ValueError("--vault is recovery-only; pass --recovery")
    if not args.recovery:
        raise ValueError(
            f"canonical seam missing: {seam}. Normal mode refuses Drive reads and writes."
        )
    reason = (args.reason or "").strip()
    if not reason:
        raise ValueError("--recovery requires a nonblank --reason")
    vault = Path(args.vault or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT)
    print(f"RECOVERY MODE - NONCANONICAL Drive projection - reason: {reason}", file=sys.stderr)
    print(f"RECOVERY MODE - Drive root: {vault}", file=sys.stderr)
    return vault
