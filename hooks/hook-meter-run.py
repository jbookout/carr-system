#!/usr/bin/env python3
"""Run one hook through the shared event runtime and optional meter."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_execution import cli

if __name__ == "__main__":
    cli()
