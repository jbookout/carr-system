#!/usr/bin/env python3
"""Hermetic checks for the one connection tools/rotate-credential.py may mint.

No database, no network, no db.env: every check below runs against the pure
functions and the argument parser. The DSN construction is the part worth
pinning, because a backup credential that is scoped wrong still connects — it
just quietly reads or misses the wrong things, and nothing fails until a
restore needs the table that was not there.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rotate_credential",
                                              REPO / "tools" / "rotate-credential.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("rotate-credential-mint-selftest: cannot load tools/rotate-credential.py")
# Any because the module is loaded by path: a type checker cannot see the
# attributes of a file it was never told to follow.
rc: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rc)

FAILURES: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILURES.append(label)


# The jobs peer deliberately carries a second query parameter; the exporter peer
# deliberately does not. A mint must produce the same result from either.
JOBS = "postgresql://carr_jobs:pw1@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
EXPORTER = "postgresql://app_exporter_local:pw2@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require"

PW = "A" * 40


def main() -> int:
    check("carr_backup is the only mintable role", rc.MINTABLE == {"carr_backup"})
    check("carr_backup maps to the env var backup-dump.sh reads",
          rc.ROLE_ENV.get("carr_backup") == "CARR_DB_BACKUP_URL")

    from_exporter = rc.mint_url("carr_backup", {"CARR_DB_EXPORTER_URL": EXPORTER}, PW)
    from_jobs = rc.mint_url("carr_backup", {"CARR_DB_JOBS_URL": JOBS}, PW)

    check("minted URL authenticates as carr_backup, which backup-dump.sh enforces",
          "://carr_backup:" in from_exporter)
    check("host and database are copied from the peer",
          "@ep-x-123.us-east-2.aws.neon.tech/neondb?" in from_exporter)
    check("the peer's password never survives into the minted URL",
          "pw1" not in from_jobs and "pw2" not in from_exporter)
    # The whole point of setting rather than inheriting the query string.
    check("query string is SET, so a jobs peer does not drag channel_binding along",
          from_jobs.endswith("?sslmode=require") and "channel_binding" not in from_jobs)
    check("either peer yields a byte-identical result", from_jobs == from_exporter)

    # db.env is read by two parsers and therefore has two contracts. Python
    # readers split on '=' and strip quotes and do not care; zsh SOURCES the
    # file, so an unquoted & in a DSN is a background operator rather than a
    # character. A minted line has to survive the shell one.
    #
    # This pins that property for the minted value only. It is NOT the
    # every-consumer check the enforceability audit sketches for that rule, so
    # the audit row stays where it is rather than being promoted off the back
    # of a narrower test than it asks for.
    quoted = rc.shell_quote(from_jobs)
    check("minted value is single-quoted for the shell parser",
          quoted.startswith("'") and quoted.endswith("'"))

    # A peer that is present but malformed must not silently become a guess.
    try:
        rc.mint_url("carr_backup", {"CARR_DB_EXPORTER_URL": "not-a-dsn"}, PW)
        check("a malformed peer refuses rather than guessing a host", False)
    except SystemExit:
        check("a malformed peer refuses rather than guessing a host", True)

    try:
        rc.mint_url("carr_backup", {}, PW)
        check("no peer at all refuses rather than inventing a host", False)
    except SystemExit:
        check("no peer at all refuses rather than inventing a host", True)

    # Both ends of the same role, or the one still working breaks.
    src = (REPO / "tools" / "rotate-credential.py").read_text(encoding="utf-8")
    check("the GitHub secret is passed on stdin, never on a command line",
          'input=url' in src and '"gh", "secret", "set"' in src)
    check("a carr_backup run without --github-secret warns that the cloud end is stale",
          "the cloud nightly still holds the OLD password" in src)
    check("a failed secret write says db.env already moved",
          "db.env IS WRITTEN but" in src)
    check("no other role gained a mint path",
          "role not in MINTABLE" in src)

    # BEHAVIOUR, not a grep. An earlier cut of this file searched the source for
    # the refusal sentence and failed only because the literal is split across
    # two lines — the check was testing how the message is typed, not what the
    # tool does. These drive the real function against a temp db.env instead.
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        env_file = Path(raw) / "db.env"
        env_file.write_text(f"CARR_DB_EXPORTER_URL='{EXPORTER}'\n", encoding="utf-8")
        real_env_path, rc.ENV_PATH = rc.ENV_PATH, str(env_file)
        # Set so the owner check upstream passes and the run reaches the branch
        # under test; nothing here ever opens a connection.
        os.environ["DATABASE_URL"] = "postgresql://owner:pw@host/db"
        try:
            try:
                rc.rotate_role("carr_backup", generate=False)
                check("minting refuses without --generate", False)
            except SystemExit as e:
                check("minting refuses without --generate", "--generate" in str(e))

            try:
                rc.rotate_role("carr_jobs", generate=True)
                check("a non-mintable role with no env line still refuses", False)
            except SystemExit as e:
                check("a non-mintable role with no env line still refuses",
                      "does not mint a connection" in str(e))
        finally:
            rc.ENV_PATH = real_env_path
            os.environ.pop("DATABASE_URL", None)

    if FAILURES:
        print(f"rotate-credential-mint-selftest: {len(FAILURES)} FAILED")
        return 1
    print("rotate-credential-mint-selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
