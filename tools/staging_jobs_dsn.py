#!/usr/bin/env python3
"""An EPHEMERAL carr_jobs identity for the ISOLATED STAGING project.

WHY THIS EXISTS, measured 2026-08-18. PR #288 ("Enforce routine database
credential boundaries", cd3d7386) moved `tools/ops-record.py run` and `assess`
from connect("write") to connect("routine"). Routine mode reads CARR_DB_JOBS_URL
and nothing else, and then proves the connection really is carr_jobs. That is
the right boundary for the seven launchd jobs it was written for, and it broke
the one path that recorded against staging.

The break, exactly. `tools/db-tap.py --project staging run ...` — the documented
tier-2 invocation of ops/run-scheduled-selftest.py and
ops/scheduled-run-record-selftest.py — exports DATABASE_URL and only
DATABASE_URL. The recorder no longer looks at it, so ops-record.py's own
_load_db_env() falls through to ~/.config/carr/db.env and its setdefault
supplies the PRODUCTION jobs DSN. Tier 2 then aimed its throwaway probe rows at
production's registry, which has never heard of the probe service, so the
recorder refused EX_CONFIG (78), nothing landed anywhere, and every tier-2
read-back against staging failed. Verified live 2026-08-18: three tier-2
failures in run-scheduled-selftest, tier 1 entirely green.

WHY MINTING RATHER THAN A STORED CREDENTIAL. Isolated staging ALREADY has the
identity: db/schema.sql's role preamble creates carr_jobs LOGIN on any fresh
rebuild, and the generated CARR GRANTS section gives it insert on ops.run and
select on ops.service and ops.service_environment — the exact authority the
recorder needs. Probed live 2026-08-18; nothing about the role or its grants was
missing. The ONLY thing absent is a password, because that preamble generates a
random placeholder in-process and deliberately never selects, logs, or writes it
down.

So the gap is one password for a role that exists, in a project that is rebuilt
whenever it needs to be and holds nothing that cannot be regenerated. A third
profile in tools/staging_database_credential.py would answer that with a
long-lived secret on disk, a provisioner, and a cleanup path — real machinery,
and a standing credential, for an identity only two selftests ever assume. This
mints one per run, holds it in memory, and lets the next run mint another. There
is nothing to rotate, leak, or forget to revoke.

WHAT KEEPS THIS OFF PRODUCTION. Not a label and not an environment variable
claiming to be staging. Before anything is altered, the caller's owner DSN must
resolve, host for host, to the read-write endpoint db-tap itself derives for the
carr-staging project — reusing tools/db-tap.py's own resolver, which already
refuses when staging resolves to production's pinned project id. A DSN pointing
anywhere else raises StagingJobsRefusal and mints nothing. That is strictly
stronger than what tier 2 had before this file: "DATABASE_URL is set, and
db-tap --project staging is what sets it" was a convention, not a check.
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import sys
from typing import Callable, Mapping
from urllib.parse import unquote, urlsplit

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.loadpy import load_module_from_path
PROVISIONING = REPO / "ops/config/control-plane-provisioning.v1.json"
ROLE = "carr_jobs"

# libpq reads these out of the environment and would quietly re-aim or
# re-authenticate a connection whose DSN looks correct. The routine credential
# names come from the provisioning contract below rather than a second copy
# here; these are the libpq-level leaks that contract does not speak to.
PG_LEAK_VARS = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS",
)


class StagingJobsRefusal(RuntimeError):
    """The target is not isolated staging, or the identity cannot be minted."""


_db_tap = load_module_from_path("staging_jobs_db_tap", str(REPO / "tools/db-tap.py"))
# db-tap already holds a loaded handle on the credential helper (its own
# _load_credential_module), so take that one rather than loading a second copy
# whose dataclasses would compare unequal to the first's.
_credential = _db_tap.staging_credential


def _neonctl_env() -> dict[str, str]:
    """The environment db-tap's own resolver expects — same PATH prefix and same
    NEON_API_KEY derivation, so an expired browser login fails here the way it
    fails there rather than in some new shape."""
    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    key = _db_tap._neon_api_key()
    if key:
        env["NEON_API_KEY"] = key
    return env


def verify_staging_owner(owner_dsn: str) -> tuple[str, int, str]:
    """Prove owner_dsn addresses isolated staging's read-write endpoint.

    Returns (endpoint_host, port, database) so a caller can validate whatever it
    builds against the same pinned target. Raises StagingJobsRefusal otherwise —
    including for a Neon BRANCH of production, whose host is a different endpoint
    than the one resolved here even though its data is production's.
    """
    try:
        parsed = urlsplit(owner_dsn)
        port = parsed.port or 5432
    except ValueError as exc:
        raise StagingJobsRefusal("owner DSN is not a parseable URI") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise StagingJobsRefusal("owner DSN names no host")
    try:
        _project, _branch, _endpoint, endpoint_host = _db_tap._staging_runtime_target(_neonctl_env())
    except SystemExit as exc:
        # db-tap's resolver refuses by sys.exit with a sentence. Carry that
        # sentence rather than replacing it with a vaguer one of our own.
        raise StagingJobsRefusal(f"isolated staging did not resolve: {exc}") from exc
    # A pooled host is the same endpoint with -pooler on its first label; Neon
    # serves both and neonctl returns the direct one. Accept either rather than
    # refusing a caller who legitimately holds the pooled DSN.
    if host not in {endpoint_host, endpoint_host.replace(".", "-pooler.", 1)}:
        raise StagingJobsRefusal(
            "owner DSN does not address the isolated staging read-write endpoint — "
            "refusing to mint a carr_jobs credential against it")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise StagingJobsRefusal("owner DSN names no database")
    return endpoint_host, port, database


def mint(owner_dsn: str,
         password_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48)) -> str:
    """Give isolated staging's existing carr_jobs role a fresh password and
    return its DSN. Never written to disk, never printed, never logged."""
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise StagingJobsRefusal("psycopg is required to mint a staging jobs credential") from exc

    endpoint_host, port, database = verify_staging_owner(owner_dsn)
    password = password_factory()
    if len(password.encode()) < 32 or any(ch.isspace() for ch in password):
        raise StagingJobsRefusal(
            "generated password is not at least 256 bits of non-whitespace material")

    # build_role_uri refuses an owner_dsn that is not neondb_owner's, which is a
    # second, independent reason a production JOBS credential can never be the
    # input here: it is not an owner URI at all.
    try:
        value = _credential.build_role_uri(owner_dsn, ROLE, password)
        _credential.validate_uri(
            value, role_name=ROLE, expected_endpoint=endpoint_host,
            expected_port=port, expected_database=database,
        )
    except _credential.CredentialRefusal as exc:
        raise StagingJobsRefusal(str(exc)) from exc

    with psycopg.connect(owner_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select 1 from pg_roles where rolname = %s", (ROLE,))
        if cur.fetchone() is None:
            raise StagingJobsRefusal(
                f"isolated staging has no {ROLE} role — rebuild it from db/schema.sql, "
                "whose role preamble creates it LOGIN")
        cur.execute(sql.SQL("alter role {} with login password {}").format(
            sql.Identifier(ROLE), sql.Literal(password)))

    # Prove the minted credential authenticates as the role ops-record.py's
    # connect("routine") will demand, HERE, where the failure is one sentence —
    # rather than inside a wrapper subprocess whose recorder output is a log line.
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select session_user, current_user")
        if cur.fetchone() != (ROLE, ROLE):
            raise StagingJobsRefusal(
                f"minted staging credential does not authenticate as {ROLE}")
    return value


def _forbidden_names() -> tuple[str, ...]:
    """The credential names a routine must NOT carry, read from the provisioning
    contract rather than copied. ops/config/control-plane-provisioning.v1.json's
    routine_jobs block is the single source for this list; a name added there
    starts being stripped here without an edit."""
    try:
        contract = json.loads(PROVISIONING.read_text(encoding="utf-8"))["routine_jobs"]
    except (OSError, ValueError, KeyError) as exc:
        raise StagingJobsRefusal(
            "routine_jobs provisioning contract is unreadable") from exc
    names = contract.get("forbidden_environment_variables")
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        raise StagingJobsRefusal("routine_jobs forbidden_environment_variables is malformed")
    key = contract.get("credential_env")
    if key != "CARR_DB_JOBS_URL":
        raise StagingJobsRefusal("routine_jobs credential_env is not CARR_DB_JOBS_URL")
    return tuple(names)


def routine_env(base: Mapping[str, str], jobs_dsn: str) -> dict[str, str]:
    """The environment a routine actually runs with in production: the jobs
    credential and no broader one.

    Tier 2 keeps the owner DSN for its own setup and read-back — it needs to
    insert a probe service and read ops.run back, neither of which carr_jobs may
    do — but the WRAPPED child gets this instead. That is what makes the tier
    exercise the real credential shape: if the recorder could still reach an
    inherited owner credential, a regression in routine-mode selection would
    pass this suite silently, which is precisely the failure PR #288 introduced
    and nothing caught.
    """
    env = dict(base)
    for name in _forbidden_names() + PG_LEAK_VARS:
        env.pop(name, None)
    env["CARR_DB_JOBS_URL"] = jobs_dsn
    return env
