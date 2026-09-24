#!/usr/bin/env python3
# doctrine: runbook
"""Fail if any tracked file carries a known client name (WR-000049).

Joe ruled on 2026-09-03 that this repository stays public, so the tree must
carry no client record. no-client-deliverables-gate.py catches the SHAPE of a
client file (a pre-tour packet folder); this gate catches the NAMES (client,
practice, lead, deal and counterparty-person names collected from the record
layer) wherever they turn up: a code comment, a test fixture, a mockup row, a
doctrine example.

NOTHING DERIVED FROM A NAME IS COMMITTED WITHOUT A SECRET KEY. A plain sha256
of a name is not protection: anyone with a surname dictionary can hash guesses
and confirm who is a client. So the gate has two sources for its list and
refuses to invent a third:

  LOCAL mode (the pre-push floor on a partner machine). The plain name list is a
  GITIGNORED local file, one name per line:
      $CARR_CLIENT_NAMES, else ops/config/client-names.local.txt in this
      checkout, else ~/carr-system/ops/config/client-names.local.txt
  It is built from the record-layer read verbs and never committed; if it were
  ever tracked, this gate would fail on every line of it.

  HMAC mode (hosted CI, and any machine without the local list). The committed
  ops/config/client-name-hmacs.v1.json holds HMAC-SHA256(key, canonical name)
  for every name. Without the key the file confirms nothing. The key comes only
  from the environment variable CARR_NAME_GUARD_KEY (a GitHub Actions secret
  that a human sets); this gate never creates, prints or stores it. The file
  carries a key-check value so a wrong key fails loudly instead of matching
  nothing.

  NEITHER available: the gate SKIPS LOUDLY. It prints a WARNING line (and a
  GitHub ::warning:: annotation under Actions) and exits 0. A silent pass would
  look like protection that is not there.

Canonical form, applied identically to the list and to the tree:

    lowercase; drop apostrophes (' and U+2019); every run of characters other
    than [a-z0-9] becomes one space; strip. "Dr. O'Neil-Smith" -> "dr oneil smith"

A name is a run of 1..max_tokens canonical tokens, so a name matches across
case, punctuation, hyphens, underscores and line-internal whitespace, and at
token boundaries only ("ann" never matches inside "annual"). File PATHS are
checked the same way as file contents.

WHERE NAMES ARE ALLOWED TO REMAIN. ops/config/client-name-allowlist.v2.json
lists FILES, never names: applied migrations (immutable, awaiting the history
purge), the generated db/schema.sql, public market data and one digest-pinned
config. An entry with file_sha256 covers the file only while its bytes are
unchanged, so any edit to it re-arms the gate; an entry without one (only the
generated schema dump) covers the path.

Output never names a match. LOCAL mode reports the line number in the local
list; HMAC mode reports a 12-hex prefix of the keyed digest.

Rebuilding the committed HMAC file, by whoever holds the key (never in CI logs):

    CARR_NAME_GUARD_KEY=... python3 ops/no-client-names-gate.py --build-hmacs

Binary files (a NUL byte in the first 8 KiB) are skipped; the deliverables gate
owns binary client material.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from typing import Callable, Iterable, Iterator

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HMACS = os.path.join(REPO, "ops", "config", "client-name-hmacs.v1.json")
ALLOW = os.path.join(REPO, "ops", "config", "client-name-allowlist.v2.json")
LOCAL_BASENAME = os.path.join("ops", "config", "client-names.local.txt")
NAMES_ENV = "CARR_CLIENT_NAMES"
KEY_ENV = "CARR_NAME_GUARD_KEY"
KEY_CHECK_MSG = b"carr-name-guard/key-check/v1"
TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower().replace("'", "").replace("’", ""))


def canonical(text: str) -> str:
    return " ".join(tokens(text))


def keyed(key: bytes) -> Callable[[str], str]:
    return lambda s: hmac.new(key, s.encode("utf-8"), hashlib.sha256).hexdigest()


def key_check(key: bytes) -> str:
    return hmac.new(key, KEY_CHECK_MSG, hashlib.sha256).hexdigest()


class NameList:
    """Matches token n-grams against a set of digests of canonical names.

    `digest` maps a canonical string to the value stored in `full`/`first`:
    the identity for LOCAL mode, keyed HMAC for HMAC mode. `label` turns a
    matched digest into the printable, name-free identifier."""

    def __init__(self, full: set[str], first: set[str], max_tokens: int,
                 digest: Callable[[str], str], label: Callable[[str], str], mode: str):
        self.full, self.first, self.max_tokens = full, first, max_tokens
        self.digest, self.label, self.mode = digest, label, mode
        self._tok_cache: dict[str, bool] = {}

    @classmethod
    def from_names(cls, names: list[str]) -> "NameList":
        index: dict[str, int] = {}
        for i, raw in enumerate(names, 1):
            c = canonical(raw)
            if c and c not in index:
                index[c] = i
        return cls(set(index), {c.split()[0] for c in index},
                   max((len(c.split()) for c in index), default=1),
                   digest=lambda s: s, label=lambda c: f"local-list line {index[c]}", mode="local")

    @classmethod
    def from_hmacs(cls, doc: dict, key: bytes) -> "NameList":
        d = keyed(key)
        return cls(set(doc["hmac_sha256"]), set(doc["first_token_hmac_sha256"]),
                   int(doc["max_tokens"]), digest=d, label=lambda h: f"name#{h[:12]}", mode="hmac")

    def _starts(self, tok: str) -> bool:
        hit = self._tok_cache.get(tok)
        if hit is None:
            hit = self._tok_cache[tok] = self.digest(tok) in self.first
        return hit

    def hits(self, text: str) -> Iterator[tuple[int, str]]:
        """(1-based line, digest) for every listed name in text."""
        for ln, line in enumerate(text.split("\n"), 1):
            toks = tokens(line)
            for i, tok in enumerate(toks):
                if not self._starts(tok):
                    continue
                for n in range(1, min(self.max_tokens, len(toks) - i) + 1):
                    h = self.digest(" ".join(toks[i:i + n]))
                    if h in self.full:
                        yield ln, h


def local_names_path() -> str | None:
    override = os.environ.get(NAMES_ENV)
    if override:
        return override if os.path.isfile(override) else None
    for base in (REPO, os.path.expanduser("~/carr-system")):
        candidate = os.path.join(base, LOCAL_BASENAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def read_local_names(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [l.rstrip("\n") for l in fh if l.strip() and not l.lstrip().startswith("#")]


def build_hmac_doc(names: list[str], key: bytes) -> dict:
    d = keyed(key)
    canon = sorted({c for c in (canonical(n) for n in names) if c})
    return {
        "schema": "carr.client-name-hmacs/v1",
        "wr": "WR-000049",
        "algorithm": "HMAC-SHA256(key = env CARR_NAME_GUARD_KEY, message = canonical name, utf-8)",
        "normalization": "lowercase; drop ' and U+2019; each run of characters outside [a-z0-9] becomes one space; strip",
        "key_check": key_check(key),
        "max_tokens": max((len(c.split()) for c in canon), default=1),
        "first_token_hmac_sha256": sorted({d(c.split()[0]) for c in canon}),
        "hmac_sha256": sorted({d(c) for c in canon}),
    }


def load_allow(path: str = ALLOW) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for e in doc["entries"]:
        if set(e) - {"path", "file_sha256", "reason"} or not e.get("reason"):
            raise ValueError(f"{path}: allowlist entries are FILES (path, optional file_sha256, "
                             f"reason) and never name digests: {e.get('path')}")
    return doc["entries"]


def file_sha(repo: str, rel: str) -> str | None:
    path = os.path.join(repo, rel)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tracked(repo: str = REPO) -> list[str]:
    # scrubbed_env: an inherited GIT_DIR (every git hook exports one) would
    # outrank cwd and list some other repository's files (ops/git_env.py).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from git_env import scrubbed_env
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True,
                         env=scrubbed_env()).stdout
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
         covered: Callable[[str], bool]) -> tuple[list[tuple[str, str, str]], set[str]]:
    """Violations as (path, line-or-'path', label) and the allowlisted paths that matched."""
    bad, used = [], set()
    for rel, text in files:
        found = [("path", h) for _, h in names.hits(rel)]
        if text is not None:
            found += [(str(ln), h) for ln, h in names.hits(text)]
        for where, h in found:
            if covered(rel):
                used.add(rel)
            else:
                bad.append((rel, where, names.label(h)))
    return bad, used


def skip_loudly(reason: str) -> int:
    msg = (f"WARNING no-client-names-gate SKIPPED: {reason}. The tree was NOT checked for "
           f"client names. Provide the gitignored local list ({LOCAL_BASENAME}) or set the "
           f"{KEY_ENV} secret (Joe's desk list).")
    print(msg)
    print(msg, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=no-client-names-gate skipped::{msg}")
    return 0


def select_names(hmacs_path: str) -> tuple[NameList | None, str]:
    """(NameList, '') or (None, why-skipped). Raises SystemExit(1) on a wrong key."""
    local = local_names_path()
    if local:
        return NameList.from_names(read_local_names(local)), ""
    key = os.environ.get(KEY_ENV, "")
    if not key:
        return None, f"no local name list and {KEY_ENV} is unset"
    if not os.path.isfile(hmacs_path):
        return None, f"{KEY_ENV} is set but {os.path.relpath(hmacs_path, REPO)} is not committed yet"
    with open(hmacs_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not hmac.compare_digest(doc.get("key_check", ""), key_check(key.encode("utf-8"))):
        print(f"no-client-names-gate: {KEY_ENV} does not match the key "
              f"{os.path.relpath(hmacs_path, REPO)} was built with (key_check mismatch). "
              "Rebuild the file with the current key, or fix the secret.", file=sys.stderr)
        raise SystemExit(1)
    return NameList.from_hmacs(doc, key.encode("utf-8")), ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-hmacs", action="store_true",
                    help=f"write the committed HMAC list from the local list and {KEY_ENV}")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--hmacs", default=HMACS, help=argparse.SUPPRESS)
    ap.add_argument("--allow", default=ALLOW, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.build_hmacs:
        local, key = local_names_path(), os.environ.get(KEY_ENV, "")
        if not local or not key:
            print(f"--build-hmacs needs the local list ({'found' if local else 'MISSING'}) "
                  f"and {KEY_ENV} ({'set' if key else 'UNSET'})", file=sys.stderr)
            return 2
        doc = build_hmac_doc(read_local_names(local), key.encode("utf-8"))
        with open(a.hmacs, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
        print(f"wrote {len(doc['hmac_sha256'])} keyed digests to {a.hmacs}")
        return 0

    names, why = select_names(a.hmacs)
    if names is None:
        return skip_loudly(why)

    entries = load_allow(a.allow)
    pins = {e["path"]: e.get("file_sha256") for e in entries}
    shas: dict[str, str | None] = {}

    def covered(rel: str) -> bool:
        if rel not in pins:
            return False
        if pins[rel] is None:
            return True
        if rel not in shas:
            shas[rel] = file_sha(a.repo, rel)
        return shas[rel] == pins[rel]

    files = ((rel, read_text(a.repo, rel)) for rel in tracked(a.repo))
    bad, used = scan(names, files, covered)
    stale = sorted(set(pins) - used)
    if stale:
        # Informational: an allowlisted file nothing needs any more should be
        # dropped so it cannot silently cover a future reintroduction.
        print(f"no-client-names-gate: {len(stale)} allowlisted file(s) no longer carry a "
              f"listed name. Prune them from {a.allow}:", file=sys.stderr)
        for rel in stale:
            print(f"  {rel}", file=sys.stderr)
    if bad:
        print("no-client-names-gate: a known client name is tracked (WR-000049 / Joe's "
              "2026-09-03 public-repo ruling). Replace it with a synthetic name, or move a "
              "live roster to a gitignored local file. An allowlisted file whose bytes "
              "changed is no longer covered:", file=sys.stderr)
        for rel, where, label in bad:
            print(f"  {rel}:{where}  {label}", file=sys.stderr)
        return 1
    print(f"no-client-names-gate: clean ({names.mode} mode). {len(names.full)} listed names, "
          f"{len(used)} allowlisted file(s) carry one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
