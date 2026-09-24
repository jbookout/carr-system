#!/usr/bin/env python3
# doctrine: runbook
"""Fail if any tracked file carries a known client name (WR-000049).

Joe ruled on 2026-09-03 that this repository stays public, so the tree must
carry no client record. no-client-deliverables-gate.py catches the SHAPE of a
client file (a pre-tour packet folder); this gate catches the NAMES — client,
practice, lead, deal and counterparty-person names collected from the record
layer — wherever they turn up: a code comment, a test fixture, a mockup row,
a doctrine example.

THE LIST IS HASHES ONLY. ops/config/client-name-hashes.v1.json holds the
sha256 of each name's canonical form and never the name, so the guard does not
republish what it guards. Canonical form, applied identically to the list and
to the tree:

    lowercase; drop apostrophes (' and U+2019); every run of characters other
    than [a-z0-9] becomes one space; strip. "Dr. O'Neil-Smith" -> "dr oneil smith"

A name is a run of 1..max_tokens canonical tokens, so a name matches across
case, punctuation, hyphens, underscores and line-internal whitespace, and at
token boundaries only ("ann" never matches inside "annual"). File PATHS are
checked the same way as file contents.

WHERE NAMES ARE ALLOWED TO REMAIN. ops/config/client-name-allowlist.v1.json
lists exact (path, sha256) pairs with a reason — applied migrations (immutable
history, awaiting the history purge), the generated db/schema.sql, and public
market data. An allowlisted pair covers that ONE name in that ONE file; the
same name anywhere else, or a new name in the same file, still fails.

FAST ON PURPOSE. It runs in pushfloor, the cheapest class. Hashing every
n-gram of the tree would be tens of millions of sha256 calls, so the list also
carries the hash of each name's FIRST token: a window is hashed only when its
first token's hash (cached per distinct token) is one of those.

Maintaining the list without writing a name down anywhere tracked:

    printf '%s\\n' "Some Name" | python3 ops/no-client-names-gate.py --hash

prints the canonical form's hashes (full and first-token) for pasting into the
list. Binary files (a NUL byte in the first 8 KiB) are skipped; the deliverables
gate owns binary client material.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Iterable, Iterator

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HASHES = os.path.join(REPO, "ops", "config", "client-name-hashes.v1.json")
ALLOW = os.path.join(REPO, "ops", "config", "client-name-allowlist.v1.json")
TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower().replace("'", "").replace("’", ""))


def canonical(text: str) -> str:
    return " ".join(tokens(text))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class NameList:
    def __init__(self, full: set[str], first: set[str], max_tokens: int):
        self.full, self.first, self.max_tokens = full, first, max_tokens
        self._tok_cache: dict[str, bool] = {}

    @classmethod
    def load(cls, path: str = HASHES) -> "NameList":
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return cls(set(doc["sha256"]), set(doc["first_token_sha256"]), int(doc["max_tokens"]))

    def _starts(self, tok: str) -> bool:
        hit = self._tok_cache.get(tok)
        if hit is None:
            hit = self._tok_cache[tok] = sha(tok) in self.first
        return hit

    def hits(self, text: str) -> Iterator[tuple[int, str]]:
        """(1-based line, sha256) for every listed name in text."""
        for ln, line in enumerate(text.split("\n"), 1):
            toks = tokens(line)
            for i, tok in enumerate(toks):
                if not self._starts(tok):
                    continue
                for n in range(1, min(self.max_tokens, len(toks) - i) + 1):
                    h = sha(" ".join(toks[i:i + n]))
                    if h in self.full:
                        yield ln, h


def load_allow(path: str = ALLOW) -> set[tuple[str, str]]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {(e["path"], e["sha256"]) for e in doc["entries"]}


def tracked(repo: str = REPO) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def read_text(repo: str, rel: str) -> str | None:
    path = os.path.join(repo, rel)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        data = fh.read()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


def scan(names: NameList, files: Iterable[tuple[str, str | None]],
         allow: set[tuple[str, str]]) -> tuple[list[tuple[str, str, str]], set[tuple[str, str]]]:
    """Violations as (path, line-or-'path', sha256) and the allow pairs that matched."""
    bad, used = [], set()
    for rel, text in files:
        found = [("path", h) for _, h in names.hits(rel)]
        if text is not None:
            found += [(str(ln), h) for ln, h in names.hits(text)]
        for where, h in found:
            if (rel, h) in allow:
                used.add((rel, h))
            else:
                bad.append((rel, where, h))
    return bad, used


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hash", action="store_true",
                    help="read names on stdin, print their canonical hashes, exit")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--hashes", default=HASHES, help=argparse.SUPPRESS)
    ap.add_argument("--allow", default=ALLOW, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.hash:
        for line in sys.stdin:
            c = canonical(line)
            if c:
                print(json.dumps({"sha256": sha(c), "first_token_sha256": sha(c.split()[0]),
                                  "tokens": len(c.split())}))
        return 0
    names = NameList.load(a.hashes)
    allow = load_allow(a.allow)
    files = ((rel, read_text(a.repo, rel)) for rel in tracked(a.repo))
    bad, used = scan(names, files, allow)
    stale = allow - used
    if stale:
        # Informational: an allowlist pair nothing needs any more should be
        # deleted so it cannot silently cover a future reintroduction.
        print(f"no-client-names-gate: {len(stale)} allowlist entr(y/ies) no longer match "
              f"anything — prune them from {a.allow}:", file=sys.stderr)
        for rel, h in sorted(stale):
            print(f"  {rel} {h[:12]}", file=sys.stderr)
    if bad:
        print("no-client-names-gate: a known client name is tracked (WR-000049 / Joe's "
              "2026-09-03 public-repo ruling). Replace it with a synthetic name, or move a "
              "live roster to a gitignored local file:", file=sys.stderr)
        for rel, where, h in bad:
            print(f"  {rel}:{where}  name#{h[:12]}", file=sys.stderr)
        return 1
    print(f"no-client-names-gate: clean — {len(names.full)} listed names, "
          f"{len(used)} allowlisted (path, name) pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
