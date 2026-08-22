#!/usr/bin/env python3
"""Validate the credential-free loopback DSN accepted by schema-snapshot.sh."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit


SCHEMES = frozenset({"postgres", "postgresql"})
HOSTS = frozenset({"127.0.0.1", "localhost"})
DATABASE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def is_allowed_local_dsn(raw: str) -> bool:
    """Accept only scheme://loopback:port/database with no alternate routing."""
    if not raw or not raw.isascii() or "%" in raw or "\\" in raw:
        return False
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in SCHEMES or hostname not in HOSTS:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.query or parsed.fragment or port is None or not 1 <= port <= 65535:
        return False
    database = parsed.path.removeprefix("/")
    if parsed.path != f"/{database}" or not DATABASE.fullmatch(database):
        return False
    # Exact reconstruction rejects case variants, extra delimiters, ambiguous
    # authority syntax, leading-zero ports, and anything urlsplit normalized.
    canonical = f"{parsed.scheme}://{hostname}:{port}/{database}"
    return raw == canonical


def main() -> int:
    # Deliberately emit nothing: a rejected value may be malformed authority
    # syntax, and the snapshot path must never echo a caller-supplied DSN.
    return 0 if is_allowed_local_dsn(os.environ.get("CARR_SCHEMA_SNAPSHOT_DSN", "")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
