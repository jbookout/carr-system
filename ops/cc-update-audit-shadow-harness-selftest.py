#!/usr/bin/env python3
"""Hermetic entrypoint for the disposable cc-update-audit shadow harness."""
import importlib.util
from pathlib import Path

path=Path(__file__).with_name("cc-update-audit-shadow-harness.py")
spec=importlib.util.spec_from_file_location("cc_shadow_harness",path)
assert spec and spec.loader
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
raise SystemExit(module.self_test())
