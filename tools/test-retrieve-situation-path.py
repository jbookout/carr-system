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


check("store pass calls the shared situation retrieval function",
      "search_doctrine_situations(" in source)
check("store pass follows the versioned default policy row",
      "'lexical-dominant-v1'" not in source and "null)" in source)
check("store pass no longer duplicates FTS rank SQL",
      "ts_rank_cd(r.search_vector" not in source and "websearch_to_tsquery('english'" not in source)
check("store results retain the live doctrine verb pointer",
      'read-doctrine {{\\"document\\":\\"{slug}\\"}}' in source)

sys.exit(bool(failures))
