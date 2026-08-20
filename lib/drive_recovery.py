"""The explicit, noncanonical Drive boundary for legacy local artifacts.

Normal callers must never obtain a Drive path from this module.  A caller that
still needs a legacy projection has to ask for recovery, say why, and label its
output.  This is deliberately a boundary, not a canonical-source adapter.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RECOVERY_VAULT = Path(
    "/Users/booko/Library/CloudStorage/"
    "GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"
)


class RecoveryArgumentError(ValueError):
    """A malformed or incomplete explicit-recovery request."""


@dataclass(frozen=True)
class RecoveryContext:
    """The parsed boundary shared by every legacy local surface."""

    recovery: bool
    reason: str | None
    vault: Path | None
    args: tuple[str, ...]


def parse_recovery_controls(argv: list[str] | tuple[str, ...], seam: str) -> RecoveryContext:
    """Strip only recovery controls while preserving all surface arguments.

    Normal mode is canonical-record-only and deliberately erases ambient Drive
    selection. Recovery is an explicit three-part act: ``--recovery``, a
    separate non-option ``--reason`` value, and optionally a separate
    ``--vault`` value. Caller-selected source modes are refused; the boundary,
    not a remembered flag, chooses records versus recovery files.
    """
    raw = list(argv)
    args: list[str] = []
    recovery = False
    reason: str | None = None
    vault_value: str | None = None
    seen: set[str] = set()
    index = 0
    while index < len(raw):
        token = raw[index]
        if token in ("--files", "--records") or token.startswith(("--files=", "--records=")):
            raise RecoveryArgumentError(
                f"{token.split('=', 1)[0]} is not caller-selectable; normal mode is canonical records "
                "and legacy files require explicit --recovery"
            )
        if token.startswith(("--recovery=", "--reason=", "--vault=")):
            raise RecoveryArgumentError(
                f"{token.split('=', 1)[0]} does not accept = form; pass separate arguments"
            )
        if token == "--recovery":
            if token in seen:
                raise RecoveryArgumentError("duplicate --recovery")
            seen.add(token)
            recovery = True
            index += 1
            continue
        if token in ("--reason", "--vault"):
            if token in seen:
                raise RecoveryArgumentError(f"duplicate {token}")
            seen.add(token)
            if index + 1 >= len(raw):
                raise RecoveryArgumentError(f"{token} requires a separate value")
            value = raw[index + 1]
            if not value.strip() or value.startswith("-"):
                raise RecoveryArgumentError(
                    f"{token} requires a nonblank, non-option-looking separate value"
                )
            if token == "--reason":
                reason = value.strip()
            else:
                vault_value = value
            index += 2
            continue
        args.append(token)
        index += 1

    if not recovery and (reason is not None or vault_value is not None):
        present = "--reason" if reason is not None else "--vault"
        raise RecoveryArgumentError(f"{present} is recovery-only; pass --recovery")
    if recovery and reason is None:
        raise RecoveryArgumentError("--recovery requires a separate nonblank --reason")

    if not recovery:
        os.environ.pop("CARR_VAULT", None)
        os.environ["CARR_SOURCE_MODE"] = "records"
        return RecoveryContext(False, None, None, tuple(args))

    vault = Path(vault_value or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT)
    os.environ["CARR_SOURCE_MODE"] = "files"
    print(f"RECOVERY MODE - NONCANONICAL Drive projection - reason: {reason}", file=sys.stderr)
    print(f"RECOVERY MODE - NONCANONICAL Drive root: {vault}", file=sys.stderr)
    return RecoveryContext(True, reason, vault, tuple(args))


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
