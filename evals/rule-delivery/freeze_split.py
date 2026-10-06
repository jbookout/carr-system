#!/usr/bin/env python3
"""Provision and guard frozen eval bundles through the existing eval front door.

Legacy split.json is historical evidence and is never overwritten. The shared
contract lives in ops/eval_split.py; subcommands are freeze, tune and load.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
import eval_split


def main():
    return eval_split.main()


if __name__ == "__main__":
    sys.exit(main())
