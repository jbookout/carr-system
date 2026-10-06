#!/usr/bin/env python3
"""CLI entry point for the read-only builder loop feed."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.routines.loop_feed import main

if __name__ == "__main__":
    raise SystemExit(main())
