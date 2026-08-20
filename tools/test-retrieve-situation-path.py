#!/usr/bin/env python3
"""Regression check: the retrieval CLI uses the same database ranker as MCP."""
from pathlib import Path
import sys


source = Path(__file__).with_name("retrieve.py").read_text()
failures = 0


def check(name, condition):
    global failures
    print(("  ok    " if condition else "  FAIL  ") + name)
    if not condition:
        failures += 1


check("store pass calls the authenticated shared situation-retrieval verb",
      '"search-doctrine", payload' in source
      and 'tools" / "call-verb.py"' in source)
check("store identity is server-derived rather than a caller actor field",
      '"actor"' not in source and '"sponsoring_human_slug"' not in source
      and "_connect" not in source)
check("store pass no longer duplicates FTS rank SQL",
      "ts_rank_cd(r.search_vector" not in source and "websearch_to_tsquery('english'" not in source)
check("store results retain the live doctrine verb pointer",
      'read-doctrine {{\\"document\\":\\"{slug}\\"}}' in source)

sys.exit(bool(failures))
