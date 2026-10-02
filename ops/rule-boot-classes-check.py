#!/usr/bin/env python3
"""rule-boot-classes-check.py — parity and budget gate for the gated rule boot.

mcp-server/src/rule-boot-classes.js (the class data rule-boot.js reads,
since a Cloudflare Worker has no filesystem at request time) must match
ops/config/rule-classes.v1.json exactly, and the boot text must fit its budget. Thin wrapper
around ops/sync-rule-boot-classes.py --check: the SAME generator that writes the
file also proves it is not stale, so the write path and the check path
cannot silently drift into two different algorithms (rule a8c55a47).

Repository content only, no database, no network -- runs in ops/ci.sh's
inventory loop alongside the other map checks.
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, \
        f"could not build a module spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_rule_boot_classes = _load_module(
    "sync_rule_boot_classes", os.path.join(HERE, "sync-rule-boot-classes.py"))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    return sync_rule_boot_classes.main(["--check", *argv])


if __name__ == "__main__":
    sys.exit(main())
