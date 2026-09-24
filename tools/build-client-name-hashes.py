#!/usr/bin/env python3
"""build-client-name-hashes.py — write ops/config/client-name-hashes.json, the
salted-hash form of the local client roster, for machines that have no roster.

WHY. ops/business_data_patterns.py checks every replay fixture against the REAL
client roster, not only against name-shaped regexes. The roster itself is a
local, untracked file and must never be committed: this is a PUBLIC repository.
A CI runner has no roster, so it checks the same normalised names through
salted SHA-256 hashes instead. A hash reveals no name to a reader of the repo;
it only answers "is this exact word run on the roster".

USAGE
    tools/build-client-name-hashes.py [--roster PATH]

With no --roster it reads the first local roster ops/business_data_patterns.py
would use ($CARR_CLIENT_ROSTER, out/client-roster.local.txt, ...). A fresh
random salt is drawn on every build, so rebuilding changes every hash.
It prints counts only, never a name.
"""
import argparse
import json
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ops import business_data_patterns as bdp  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--roster", type=Path)
    parser.add_argument("--out", type=Path, default=bdp.HASHES_PATH)
    args = parser.parse_args(argv)
    candidates = [args.roster] if args.roster else bdp.roster_candidates()
    source = next((p for p in candidates if p and p.is_file()), None)
    if source is None:
        print("build-client-name-hashes: no local roster found", file=sys.stderr)
        return 1
    keys = bdp.read_roster_file(source)
    salt = secrets.token_hex(16)
    data = {
        "schema": "client-name-hashes/v1",
        "about": "Salted SHA-256 (first 20 hex) of normalised client and practice "
                 "names; see ops/business_data_patterns.py. Rebuild with "
                 "tools/build-client-name-hashes.py. Never commit the roster itself.",
        "salt": salt,
        "lengths": sorted({len(k.split()) for k in keys}),
        "hashes": sorted({bdp.name_hash(salt, k) for k in keys}),
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"build-client-name-hashes: {len(data['hashes'])} hashes written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
