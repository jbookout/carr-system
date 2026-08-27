#!/usr/bin/env python3
"""core-rule-ids-check.py — parity gate (WR-000019 slice S11, boot diet).

mcp-server/src/core-rule-ids.js (the CORE rule id list doctrine.js reads,
since a Cloudflare Worker has no filesystem at request time) must match
ops/config/rule-triage.v1.json's `home: "core"` set exactly. Thin wrapper
around ops/sync-core-rule-ids.py --check: the SAME generator that writes the
file also proves it is not stale, so the write path and the check path
cannot silently drift into two different algorithms (rule a8c55a47).

Repository content only, no database, no network -- runs in ops/ci.sh's
inventory loop alongside the other map checks.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "sync_core_rule_ids", os.path.join(HERE, "sync-core-rule-ids.py"))
sync_core_rule_ids = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_core_rule_ids)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    return sync_core_rule_ids.main(["--check", *argv])


if __name__ == "__main__":
    sys.exit(main())
