#!/usr/bin/env python3
"""V5-F01 persistence tail — the LOCAL, DISPOSABLE PostgreSQL gate.

WHAT THIS IS FOR. Proving the parts of the F01 persistence contract that only a
real database can prove: compare-and-swap under genuine concurrency, append-only
refusal, direct-DML refusal from a non-owner runtime principal, wrong-principal
refusal, corrupt-newest-row readback, legacy preservation, and byte-for-byte
agreement between the Node canonicaliser and ops.f01_canonical_json.

WHAT IT REFUSES TO DO, deliberately and by construction:

  * It DISCOVERS NO CONNECTION. There is no environment probe, no config file
    read, no secret lookup, no credential handling of any kind. The parent
    supplies --dsn explicitly, and a DSN carrying a password refuses.
  * It RUNS ONLY AGAINST A DISPOSABLE LOCAL DATABASE. The host must be a
    numeric loopback address, the database name must match the
    f01_gate_* pattern, and --confirm-disposable must repeat that name. A DSN
    that looks remote, managed or production-shaped refuses before anything runs.
  * It SUPPLIES NO MIGRATION. The parent passes --migration for the exact
    predecessor bundle and the exact single F01 successor, in order. This gate
    hard-codes no ordinal and reserves none; 0499/v25 belongs to security
    attribution and F01's successor is v26.
  * It creates no roles. PostgreSQL roles are cluster-wide. The parent must
    preprovision scratch-cluster login principals carr_writer, carr_reader,
    carr_authority_joe and carr_authority_dell, and the owner login. This gate
    authenticates each real principal before any schema or fixture write.
    The parent must independently review the exact bootstrap, migration and SQL
    fixture files for this disposable cluster. Those files execute owner SQL;
    this harness is not a containment boundary for arbitrary scripts. Before/
    after role, membership, database, extension and procedural-language
    inventory detects persistent changes; it cannot prevent or disprove a
    temporary mutation.
  * It grants nothing, to anybody, ever. Every privilege statement below is a
    READBACK of what the reviewed migration did. Where a prerequisite grant is
    missing the gate says so and stops; it does not supply it.

CANONICAL BOOTSTRAP PREREQUISITES, none of which this gate creates, checks off a
wish list, or substitutes for. They are properties of the cluster and of the
migrations the parent passes with --migration:

  * PASSWORD-LESS LOOPBACK AUTHENTICATION. connection_env strips every PG*
    variable, points PGPASSFILE/PGSERVICEFILE at /dev/null and psql runs -w, so
    all five (or six, with --fixture-role) logins must authenticate over TCP
    loopback without a password — pg_hba `trust` on 127.0.0.1/::1 for this
    scratch cluster. This is deliberate: a gate that could read a password could
    be pointed at something that mattered.
  * THE CONTROL-PLANE AUTHORITY BOUNDARY, i.e. the canonical predecessor
    migration 0161_control_plane_authority_boundary.sql. It is what defines:
      - the NOLOGIN group role `carr_authority` (created there if absent);
      - ops.authority_actor_slug(), SECURITY DEFINER and STABLE, deriving the
        actor from session_user and returning 'joe' for carr_authority_joe and
        'dell' for carr_authority_dell, raising for anyone else. That is why
        check_wrong_principal below can name those two slugs as expected values
        rather than as an assumption: they are stated by the migration, not by
        this file;
      - REVOKE ALL ON FUNCTION ops.authority_actor_slug() FROM public, and
        GRANT EXECUTE on it TO carr_authority.
    The two authority LOGIN roles must therefore be members of carr_authority,
    which is exactly what verify_connections requires below and what makes the
    caller-rights ops.f01_require_authority_principal() reachable for them.
    The F01 schema owner must also be able to EXECUTE the helper — it owns it,
    or it is a member of carr_authority — because ops.f01_context_actor_slug()
    is SECURITY DEFINER and reaches the helper as the owner. check_helper_
    prerequisites reads all of that back after the migrations are applied, so a
    missing prerequisite is an early named failure instead of a confusing one
    three sections later.

    ORDERING NOTE the parent has to settle, not this gate: --migration files are
    applied AS THE OWNER. If the owner is the non-superuser this gate prefers, it
    may hold neither CREATEROLE nor the right to GRANT on schema public, so
    passing 0161 itself as a --migration can fail on its role creation and its
    schema grants. Have the control-plane boundary in place before the run —
    verify_connections already requires the carr_authority role to exist — and
    pass only the F01 predecessor bundle and successor here.

WHAT A PASS DOES NOT PROVE. The scratch owner is a PRECONDITION, not a subject.
Its administrative attributes and reachable memberships are observed and
reported; an owner that holds one makes every privilege proof below a statement
about a cooperative migration rather than about a boundary, and the gate records
that as a skip rather than claiming a proof it cannot make. The gate asserts no
membership policy for the owner, because the source contract states none.

No role names or connection options are discovered from the environment.
All libpq environment options are removed, password and service files disabled,
and psql never prompts for a password. The explicit URI must contain an explicit
loopback host and port, no userinfo, no query, and no fragment.

USAGE

  python3 ops/record-source-authority-local-pg-gate.py \\
      --dsn postgresql://127.0.0.1:5432/f01_gate_scratch --owner-role f01_owner \\
      --confirm-disposable f01_gate_scratch \\
      --fixture-role f01_bootstrap \\
      --migration path/to/predecessor-bundle.sql \\
      --migration path/to/00NN_f01_successor.sql \\
      --fixture mcp-server/test/record-source-authority-postgres.sql \\
      --repo-root .

  # Before the parent has bound the migration, the domain schema may be applied
  # on its own. This is a DRAFT convenience and is not the acceptance path.
  ... --domain-sql domain.sql

--fixture-role, AND WHY IT EXISTS. The SQL fixture switches identity with SET
SESSION AUTHORIZATION, which PostgreSQL allows only to a superuser — role
membership is explicitly not sufficient — so the login that APPLIES the fixture
must be one. The owner login must NOT be one, because owner_precondition records
a superuser owner as a skip: every privilege proof below would then be a
statement about a cooperative migration rather than about a boundary. Those two
requirements are not satisfiable by one login, so the parent may name a second,
ALREADY EXISTING bootstrap login here.

What that option does and does not do:

  * It is used for EXACTLY ONE call — psql -f <fixture> — and for nothing else.
    Every schema, privilege, DML, wrong-principal, canonical and race statement
    continues to run as the owner or as a carr_* principal.
  * It creates nothing, grants nothing, and relaxes no permission. The login
    must already exist, must already be a superuser, must be neither the owner
    nor any of the four fixture principals, and must hold no CATALOG membership
    of carr_authority, carr_writer or carr_reader — so applying the fixture is
    not a route to default authority. Its identity is verified the same way
    every other principal's is: same database, loopback address and port,
    session_user = current_user = the named role, database owned by --owner-role.
  * Its blast radius is captured, not assumed. A superuser can do anything in
    the database, so the ops-schema ownership, ACL, trigger-enabled and
    constraint fingerprint is read immediately before and immediately after the
    fixture applies, and a difference is a named FAILURE. That is a detection,
    not a containment: the fixture file remains a reviewed input.
  * Omitting it changes nothing. The fixture is then applied as the owner, which
    means the owner must be a superuser, which means owner_precondition records
    its skip and the run reports 4. A skip is still not acceptance.

EXIT CODES, each distinct so a caller never has to guess:

  0  migration-mode acceptance: every check ran and passed.
  1  at least one check FAILED.
  2  the gate REFUSED before proving anything.
  3  draft domain proof only — predecessor migration integration is unverified.
  4  every check that ran passed, but at least one was SKIPPED. A skip is never
     acceptance; 3 and 4 are separate codes precisely so that a full acceptance
     run with the race proof omitted cannot be mistaken for a draft proof.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import textwrap
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Disposable-database safety.
# --------------------------------------------------------------------------

DISPOSABLE_DB_PATTERN = re.compile(r"^f01_gate_[a-z0-9_]{1,40}$")
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
# Substrings that make a DSN look like something nobody should point a
# destructive fixture at. The list is deliberately blunt: a false refusal costs a
# rename, a false acceptance costs a production database.
FORBIDDEN_DSN_SUBSTRINGS = (
    "neon.tech", "amazonaws", "azure", "gcp", "rds.", "supabase", "render.com",
    "railway", "fly.dev", "cloud", "prod", "production", "staging", "live",
)


class GateRefusal(RuntimeError):
    """The gate refuses to run. Never downgraded to a warning."""


def assert_disposable_dsn(dsn: str, confirmed_name: str) -> str:
    """Return the database name, or refuse. Every branch here is a hard stop."""
    try:
        parsed = urllib.parse.urlparse(dsn)
        port = parsed.port
    except ValueError as error:
        raise GateRefusal("malformed connection URI") from error
    if parsed.scheme not in ("postgres", "postgresql"):
        raise GateRefusal("--dsn must be a postgresql:// URI")
    if any(c.isspace() for c in dsn) or "%" in dsn or "?" in dsn or "#" in dsn:
        raise GateRefusal("whitespace, escapes, query and fragment are forbidden")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise GateRefusal("userinfo/credentials are forbidden; use --owner-role")
    if not parsed.hostname or port is None or not 1 <= port <= 65535:
        raise GateRefusal("explicit loopback host and port are required")

    lowered = dsn.lower()
    for needle in FORBIDDEN_DSN_SUBSTRINGS:
        if needle in lowered:
            raise GateRefusal(
                f"--dsn contains {needle!r}, which does not look like a disposable local "
                "scratch database. This gate runs nowhere else."
            )
    host = (parsed.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise GateRefusal(
            f"--dsn host {host!r} is not a numeric loopback address; "
            "this gate runs only against a local disposable database"
        )
    name = (parsed.path or "")[1:]
    if not DISPOSABLE_DB_PATTERN.match(name):
        raise GateRefusal(
            f"database name {name!r} must match f01_gate_* so a scratch database cannot be "
            "confused with anything else"
        )
    if confirmed_name != name:
        raise GateRefusal(
            "--confirm-disposable must repeat the exact database name; "
            f"got {confirmed_name!r} for database {name!r}"
        )
    return name


# --------------------------------------------------------------------------
# Reporting.
# --------------------------------------------------------------------------

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Report:
    rows: list = field(default_factory=list)

    def record(self, status: str, section: str, label: str, detail: str = "") -> None:
        self.rows.append((status, section, label, detail))
        marker = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}[status]
        print(f"  {marker}  [{section}] {label}" + (f" — {detail}" if detail else ""), flush=True)

    def ok(self, section: str, label: str, detail: str = "") -> None:
        self.record(PASS, section, label, detail)

    def fail(self, section: str, label: str, detail: str = "") -> None:
        self.record(FAIL, section, label, detail)

    def skip(self, section: str, label: str, detail: str = "") -> None:
        self.record(SKIP, section, label, detail)

    def expect(self, condition: bool, section: str, label: str, detail: str = "") -> bool:
        (self.ok if condition else self.fail)(section, label, detail)
        return bool(condition)

    @property
    def failures(self) -> int:
        return sum(1 for row in self.rows if row[0] == FAIL)

    @property
    def skips(self) -> int:
        return sum(1 for row in self.rows if row[0] == SKIP)

    def summary(self) -> str:
        passed = sum(1 for row in self.rows if row[0] == PASS)
        return (f"{passed} passed, {self.failures} failed, {self.skips} skipped "
                f"({len(self.rows)} checks)")


# --------------------------------------------------------------------------
# psql plumbing. No driver dependency: the gate must run on a bare machine.
# --------------------------------------------------------------------------

def connection_env() -> dict:
    """No inherited libpq redirect, password, service or options source."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    env.update(PGPASSFILE=os.devnull, PGSERVICEFILE=os.devnull,
               PGSYSCONFDIR="/nonexistent-f01-gate", PGCONNECT_TIMEOUT="5",
               PGSSLMODE="disable", PGGSSENCMODE="disable")
    return env


class Psql:
    def __init__(self, dsn: str, binary: str, owner_role: str):
        self.dsn = dsn
        self.binary = binary
        self.owner_role = owner_role

    def _base(self, user: str | None) -> list:
        return [self.binary, self.dsn, "-X", "-w", "-q", "-v", "ON_ERROR_STOP=1",
                "-v", "VERBOSITY=verbose", "-U", user or self.owner_role]

    def scalar(self, sql: str, user: str | None = None) -> str:
        """One value, as text. Raises CalledProcessError on a database error."""
        proc = subprocess.run(
            self._base(user) + ["-A", "-t", "-c", sql],
            capture_output=True, text=True, check=True, env=connection_env(), timeout=30,
        )
        return proc.stdout.strip()

    def run(self, sql: str, user: str | None = None) -> subprocess.CompletedProcess:
        """Run SQL and return the completed process WITHOUT raising."""
        return subprocess.run(
            self._base(user) + ["-A", "-t", "-c", sql],
            capture_output=True, text=True, check=False, env=connection_env(), timeout=120,
        )

    def run_file(self, path: Path, user: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._base(user) + ["-A", "-t", "-f", str(path)],
            capture_output=True, text=True, check=False, env=connection_env(), timeout=120,
        )


class PsqlSession:
    """One long-lived psql process, so two of them can genuinely race.

    Statements are written to stdin and terminated with an echoed marker, which
    is how the gate knows a statement finished — and, crucially, how it can send
    a statement that BLOCKS on a lock, do something else, and only then wait.

    EVERY READ IS BOUNDED. Output is drained by a reader thread, so collect()
    waits on a deadline rather than on the server. statement_timeout covers only
    the statements issued after it is set, and covers nothing at all if the
    server accepts the connection and then never answers; an unbounded read
    would hang the gate and give the parent no verdict, which is strictly worse
    than a named failure. Exceeding the deadline raises GateRefusal, and the
    caller records it as a FAILED check.
    """

    MARKER = "--f01-gate-marker--"
    READ_TIMEOUT = 60.0

    def __init__(self, dsn: str, binary: str, name: str, user: str | None = None):
        argv = [binary, dsn, "-X", "-w", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=0",
                "-v", "VERBOSITY=verbose"]
        if user:
            argv += ["-U", user]
        self.name = name
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, env=connection_env(),
        )
        self.lines: queue.Queue = queue.Queue()
        self.eof = False
        self.reader = threading.Thread(target=self._pump, name=f"f01-gate-{name}", daemon=True)
        self.reader.start()

    def _pump(self) -> None:
        """Drain stdout forever; None is the end-of-stream sentinel."""
        try:
            for line in self.proc.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def send(self, sql: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(sql.rstrip().rstrip(";") + ";\n")
        self.proc.stdin.write(f"\\echo {self.MARKER}\n")
        self.proc.stdin.flush()

    def collect(self, timeout: float | None = None) -> str:
        deadline = time.monotonic() + (self.READ_TIMEOUT if timeout is None else timeout)
        lines = []
        while True:
            # A closed stream answers immediately, exactly as a dead pipe used to:
            # waiting out the deadline once per probe would turn one dead session
            # into a very slow run rather than a fast failure.
            if self.eof and self.lines.empty():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GateRefusal(f"session {self.name} returned no marker before the read deadline")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise GateRefusal(
                    f"session {self.name} returned no marker before the read deadline") from None
            if line is None:
                self.eof = True
                break
            if line.strip() == self.MARKER:
                break
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def execute(self, sql: str) -> str:
        self.send(sql)
        return self.collect()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.write("\\q\n")
                self.proc.stdin.flush()
                self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
            try:  # a killed process is still reaped, never left as a zombie
                self.proc.wait(timeout=5)
            except Exception:
                pass


# --------------------------------------------------------------------------
# Canonical-byte cases. The database must never learn these from itself, so the
# gate computes them in Node and only then asks PostgreSQL for its own answer.
# --------------------------------------------------------------------------

CANONICAL_CASES = [
    '{"a":1,"b":"x"}',
    '{"b":1,"a":2}',
    '{"k":null}',
    '{"k":true,"j":false}',
    '{"k":[1,2,3]}',
    '{"k":[]}',
    '{"k":{}}',
    '{"k":{"nested":{"deep":[1,{"z":0,"a":1}]}}}',
    '{"k":0.75}',
    '{"k":1e-7}',
    '{"k":0.000001}',
    '{"k":1e-30}',
    '{"k":5e-324}',
    '{"k":0}',
    '{"k":-1}',
    '{"k":9007199254740991}',
    '{"k":1024}',
    # Unicode: NFC accents, CJK, an astral character, and a key set whose sort
    # order differs between UTF-16 code units and code points.
    '{"\\u00e9":"caf\\u00e9"}',
    '{"k":"\\u4e2d\\u6587"}',
    '{"k":"\\ud83d\\uddc2"}',
    '{"\\uff00":2,"\\ud800\\udc00":1}',
    '{"k":"\\u007f"}',
    # JSON escapes, including the two that MUST be escaped and the five short
    # forms.
    '{"k":"quote\\"and\\\\slash"}',
    '{"k":"line\\nbreak"}',
    '{"k":"tab\\there"}',
    '{"k":"cr\\rlf\\n"}',
    '{"k":"\\b\\f"}',
    '{"k":"\\u001f"}',
    '{"k":""}',
    # Timestamp boundaries, stored as text so no parser can renormalize them.
    '{"observed_at":"2026-02-28T23:59:59.999Z"}',
    '{"observed_at":"2028-02-29T00:00:00Z"}',
    '{"observed_at":"2026-09-09T12:00:00-07:00"}',
    '{"observed_at":"2026-09-09T12:00:00+13:45"}',
    '{"observed_at":"2026-01-01T00:00:00.000Z"}',
    '{"observed_at":"2026-12-31T23:59:59.123456789Z"}',
]

NODE_CANONICAL_SCRIPT = """\
import { canonicalJson, digest } from "%(artifact_trust)s";
const cases = JSON.parse(process.argv[2]);
const out = cases.map(raw => {
  const value = JSON.parse(raw);
  return { raw, canonical: canonicalJson(value), digest: digest(value) };
});
process.stdout.write(JSON.stringify(out));
"""


def node_canonical(cases: list, repo_root: Path, node_binary: str) -> list:
    artifact_trust = (repo_root / "mcp-server" / "src" / "artifact-trust.js").resolve()
    if not artifact_trust.exists():
        raise GateRefusal(f"cannot find {artifact_trust}; pass --repo-root")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "f01-canonical.mjs"
        script.write_text(NODE_CANONICAL_SCRIPT % {"artifact_trust": artifact_trust.as_uri()},
                          encoding="utf-8")
        proc = subprocess.run(
            [node_binary, str(script), json.dumps(cases)],
            capture_output=True, text=True, check=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if k in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR")},
        )
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# The gate steps.
# --------------------------------------------------------------------------

# The ACL is read from pg_class.relacl and pg_attribute.attacl, NOT from
# information_schema.role_table_grants: that view shows only grants where the
# current user is the grantor, the grantee, or a member of the grantee role, so a
# migration granting on a legacy table to an unrelated third role would be
# invisible before and after and the "unchanged" row would be vacuous. The
# catalog columns are visible regardless of who holds the grant.
LEGACY_FINGERPRINT_SQL = """
SELECT coalesce(string_agg(fingerprint, ' || ' ORDER BY t), 'absent')
FROM (
  SELECT t,
         CASE WHEN to_regclass('public.' || t) IS NULL THEN t || '=absent'
         ELSE t || '=' ||
           coalesce((SELECT string_agg(column_name || ':' || data_type || ':' || is_nullable,
                                       ',' ORDER BY ordinal_position)
                       FROM information_schema.columns
                      WHERE table_schema = 'public' AND table_name = t), '') || ';' ||
           coalesce((SELECT string_agg(conname || ':' || pg_get_constraintdef(oid),
                                       ',' ORDER BY conname)
                       FROM pg_constraint
                      WHERE conrelid = ('public.' || t)::regclass), '') || ';acl=' ||
           coalesce((SELECT coalesce(c.relacl::text, 'owner-default')
                            || ':rls=' || c.relrowsecurity::text || c.relforcerowsecurity::text
                       FROM pg_class c WHERE c.oid = ('public.' || t)::regclass), '') || ';colacl=' ||
           coalesce((SELECT string_agg(a.attname || ':' || a.attacl::text, ',' ORDER BY a.attnum)
                       FROM pg_attribute a
                      WHERE a.attrelid = ('public.' || t)::regclass
                        AND a.attnum > 0 AND NOT a.attisdropped
                        AND a.attacl IS NOT NULL), '') || ';trg=' ||
           coalesce((SELECT string_agg(g.tgname || ':' || pg_get_triggerdef(g.oid),
                                       ',' ORDER BY g.tgname)
                       FROM pg_trigger g
                      WHERE g.tgrelid = ('public.' || t)::regclass
                        AND NOT g.tgisinternal), '') || ';rule=' ||
           coalesce((SELECT string_agg(w.rulename || ':' || pg_get_ruledef(w.oid),
                                       ',' ORDER BY w.rulename)
                       FROM pg_rewrite w
                      WHERE w.ev_class = ('public.' || t)::regclass), '')
         END AS fingerprint
    FROM unnest(ARRAY['record_source', 'document']) AS t
) s;
"""

def legacy_rowcounts(psql: Psql) -> str:
    counts = []
    for table in ('record_source', 'document'):
        exists = psql.scalar(f"SELECT to_regclass('public.{table}') IS NOT NULL") == 't'
        count = psql.scalar(f'SELECT count(*) FROM public.{table}') if exists else '-1'
        counts.append(f'{table}={count}')
    return ','.join(counts)


CONTEXT_SQL = "SELECT set_config('carr.acting_actor_slug','joe',false)"


def reachable_roles(psql: Psql, role: str) -> list:
    """Every role reachable by membership, whether or not INHERIT is set.

    has_*_privilege answers for the CURRENT privilege set, so a NOINHERIT member
    that can reach a role by SET ROLE would look unprivileged. This walk follows
    membership itself, which is the thing that actually bounds the principal.
    """
    return json.loads(psql.scalar(
        "WITH RECURSIVE reachable(oid) AS ("
        "SELECT roleid FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname="
        + sql_literal(role) + ") UNION SELECT m.roleid FROM pg_auth_members m "
        "JOIN reachable x ON m.member=x.oid) "
        "SELECT coalesce(json_agg(json_build_object('name',r.rolname,"
        "'admin',r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls) "
        "ORDER BY r.rolname),'[]'::json) "
        "FROM reachable x JOIN pg_roles r ON r.oid=x.oid"))


IDENTITY_SQL = ("SELECT json_build_object('db',current_database(),"
    "'session',session_user,'current',current_user,"
    "'address',host(inet_server_addr()),'port',inet_server_port(),"
    "'owner',pg_get_userbyid(d.datdba),'super',r.rolsuper,"
    "'createrole',r.rolcreaterole,'createdb',r.rolcreatedb,"
    "'replication',r.rolreplication,'bypassrls',r.rolbypassrls,"
    "'strings',current_setting('standard_conforming_strings'),"
    "'authority',pg_has_role(session_user,'carr_authority','member')) "
    "FROM pg_database d JOIN pg_roles r ON r.rolname=session_user "
    "WHERE d.datname=current_database()")


def identity_mismatch(row: dict, role: str, psql: Psql, db_name: str) -> bool:
    """The connection is the one that was asked for, on this loopback, in this database."""
    parsed = urllib.parse.urlparse(psql.dsn)
    return (row['db'] != db_name or row['session'] != role or row['current'] != role
            or row['address'] not in LOOPBACK_HOSTS or row['port'] != parsed.port
            or row['owner'] != psql.owner_role)


def verify_connections(psql: Psql, db_name: str) -> None:
    """Authenticate every fixture role and inspect server identity before writes."""
    if psql.owner_role in FIXTURE_ROLES or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', psql.owner_role):
        raise GateRefusal('owner must be a distinct explicit scratch-owner role')
    # The group role is a BOOTSTRAP PREREQUISITE (0161), not something this gate
    # invents or supplies, and pg_has_role errors outright on a role that does not
    # exist. Naming it here turns "REFUSED: connection/principal preflight:
    # CalledProcessError" into a sentence the parent can act on.
    if psql.scalar("SELECT count(*) FROM pg_roles WHERE rolname='carr_authority'") != '1':
        raise GateRefusal(
            "the group role carr_authority does not exist. It is a bootstrap prerequisite: "
            "0161_control_plane_authority_boundary.sql creates it and grants it EXECUTE on "
            "ops.authority_actor_slug(), and the two authority logins must be members of it. "
            "This gate creates no role and grants nothing")
    for role in (psql.owner_role, 'carr_writer', 'carr_reader',
                 'carr_authority_joe', 'carr_authority_dell'):
        row = json.loads(psql.scalar(IDENTITY_SQL, user=role))
        if identity_mismatch(row, role, psql, db_name):
            raise GateRefusal(f"actual connection identity mismatch for {role}")
        # sql_literal escapes by DOUBLING the quote, which is sufficient only
        # while backslashes are literal. With standard_conforming_strings off, a
        # backslash in a role name, a catalog identifier or a fixture JSON
        # payload changes the bytes the server parses.
        if row['strings'] != 'on':
            raise GateRefusal(
                f"standard_conforming_strings is {row['strings']!r} for {role}; "
                "literal quoting in this gate assumes 'on'")
        if role != psql.owner_role and (row['super'] or row['createrole'] or row['createdb']
                or row.get('replication') or row.get('bypassrls')):
            raise GateRefusal(f"fixture principal {role} has administrative privileges")
        if role in ('carr_writer', 'carr_reader') and row['authority']:
            raise GateRefusal(f"ordinary fixture principal {role} inherits authority")
        if role.startswith('carr_authority_') and not row['authority']:
            raise GateRefusal(f"authority fixture principal {role} lacks authority membership")
        if role != psql.owner_role:
            memberships = reachable_roles(psql, role)
            allowed = {'carr_reader'} if role in ('carr_writer','carr_reader') else {
                'carr_authority','carr_writer','carr_reader'}
            if any(m['admin'] or m['name'] not in allowed for m in memberships):
                raise GateRefusal(f'{role} has unreviewed transitive membership/SET ROLE reachability')


def verify_fixture_principal(psql: Psql, db_name: str, fixture_role: str, report: Report) -> None:
    """Authenticate the ONE login that applies the fixture, and bound what it is.

    The fixture needs SET SESSION AUTHORIZATION, which needs a superuser. The
    owner must not be one, or every privilege proof in this run is a statement
    about a cooperative migration. Those cannot be the same login, so this is the
    second one — supplied by the parent, already existing, used for exactly one
    psql -f and nothing else.

    Nothing here creates, grants or relaxes anything. It refuses a login that is
    the owner, that is one of the four fixture principals, that is not actually a
    superuser (in which case the fixture would block on its own bootstrap check
    anyway, three hundred statements later and less clearly), or that carries a
    CATALOG membership of an F01 runtime role — which is the shape "applying the
    fixture" would take if it were also a route to default authority.
    """
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', fixture_role):
        raise GateRefusal('--fixture-role must be a plain lower-case role name')
    if fixture_role in FIXTURE_ROLES or fixture_role == psql.owner_role:
        raise GateRefusal(
            '--fixture-role must be distinct from --owner-role and from all four F01 runtime '
            'principals; the whole point of the option is that the fixture applier and the '
            'schema owner are different logins')
    row = json.loads(psql.scalar(IDENTITY_SQL, user=fixture_role))
    if identity_mismatch(row, fixture_role, psql, db_name):
        raise GateRefusal(f'actual connection identity mismatch for {fixture_role}')
    if row['strings'] != 'on':
        raise GateRefusal(f"standard_conforming_strings is {row['strings']!r} for {fixture_role}")
    if not row['super']:
        raise GateRefusal(
            f'--fixture-role {fixture_role} is not a superuser, so it cannot SET SESSION '
            'AUTHORIZATION and the fixture would block. This gate does not grant it anything')
    # pg_has_role answers TRUE for a superuser against every role, so an effective
    # membership probe here would be vacuous. The catalog walk is not: it reports
    # the memberships somebody actually granted.
    memberships = [m['name'] for m in reachable_roles(psql, fixture_role)]
    granted = sorted(set(memberships) & (set(FIXTURE_ROLES) | {'carr_authority'}))
    if granted:
        raise GateRefusal(
            f'--fixture-role {fixture_role} holds catalog membership of {",".join(granted)}; '
            'the fixture applier must not also be an F01 runtime principal')
    report.ok('precondition',
              'the fixture-applying principal is a separate, verified, non-runtime superuser login',
              f'fixture_role={fixture_role} db={row["db"]} address={row["address"]}:{row["port"]} '
              f'session={row["session"]} memberships={",".join(memberships) or "none"}; '
              'used only for psql -f <fixture>, and its effect on the schema is fingerprinted '
              'before and after')


# Ownership, ACLs, trigger-enabled state and constraints for the whole ops
# schema. Deliberately NOT row contents: the fixture writes rows, and that is
# what it is for. What it must not do is leave a guard disabled, a constraint
# dropped, an object reassigned or a grant widened — which is exactly the damage
# a superuser applier could do and an owner applier could not.
OPS_FINGERPRINT_SQL = """
SELECT json_build_object(
  'relations',(SELECT coalesce(json_agg(json_build_object('r',c.oid::regclass::text,
      'kind',c.relkind,'owner',pg_get_userbyid(c.relowner),
      'acl',coalesce(c.relacl::text,'owner-default'),
      'rls',c.relrowsecurity::text||c.relforcerowsecurity::text) ORDER BY c.relname),'[]'::json)
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ops'
      AND c.relkind IN ('r','p','v','m','f')),
  'columns',(SELECT coalesce(json_agg(json_build_object('a',a.attrelid::regclass::text||'.'||a.attname,
      'acl',a.attacl::text) ORDER BY a.attrelid,a.attnum),'[]'::json)
    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='ops' AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL),
  'functions',(SELECT coalesce(json_agg(json_build_object('f',p.oid::regprocedure::text,
      'owner',pg_get_userbyid(p.proowner),'definer',p.prosecdef,
      'acl',coalesce(p.proacl::text,'owner-default')) ORDER BY p.oid::regprocedure::text),'[]'::json)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ops'),
  'triggers',(SELECT coalesce(json_agg(json_build_object('t',t.tgrelid::regclass::text||'.'||t.tgname,
      'enabled',t.tgenabled,'def',pg_get_triggerdef(t.oid)) ORDER BY t.tgrelid,t.tgname),'[]'::json)
    FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='ops' AND NOT t.tgisinternal),
  'constraints',(SELECT coalesce(json_agg(json_build_object(
      'c',c.conrelid::regclass::text||'.'||c.conname,'def',pg_get_constraintdef(c.oid))
      ORDER BY c.conrelid,c.conname),'[]'::json)
    FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
    JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='ops'),
  'schema',(SELECT json_build_object('owner',pg_get_userbyid(n.nspowner),
      'acl',coalesce(n.nspacl::text,'owner-default'))
    FROM pg_namespace n WHERE n.nspname='ops'))
"""


def ops_fingerprint(psql: Psql, phase: str) -> str:
    """The ops-schema shape, or a distinct unreadable marker — never an exception.

    An unreadable phase must not compare EQUAL to anything, including another
    unreadable phase, or a server that stopped answering would read as "nothing
    changed".
    """
    try:
        return psql.scalar(OPS_FINGERPRINT_SQL)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return f'unreadable at {phase}: {type(error).__name__}: {error}'


def owner_precondition(psql: Psql, report: Report) -> None:
    """Observe — never legislate — what the scratch owner is.

    The source contract states no membership policy for the owner, so the gate
    asserts none. What it will not do is stay silent: the owner is exempt from
    the fixture-principal attribute checks by construction (it must own the
    schema), so if it also holds a cluster administrative attribute, the whole
    privilege section describes a cooperative migration rather than a boundary.
    That is recorded as a skip, because a limit on what was proved is not a pass.
    """
    attributes = json.loads(psql.scalar(
        "SELECT coalesce(json_agg(a ORDER BY a),'[]'::json) FROM pg_roles r, "
        "LATERAL unnest(ARRAY[CASE WHEN r.rolsuper THEN 'rolsuper' END,"
        "CASE WHEN r.rolcreaterole THEN 'rolcreaterole' END,"
        "CASE WHEN r.rolcreatedb THEN 'rolcreatedb' END,"
        "CASE WHEN r.rolreplication THEN 'rolreplication' END,"
        "CASE WHEN r.rolbypassrls THEN 'rolbypassrls' END]) AS a "
        "WHERE r.rolname=" + sql_literal(psql.owner_role) + " AND a IS NOT NULL"))
    memberships = reachable_roles(psql, psql.owner_role)
    admin_memberships = [m['name'] for m in memberships if m['admin']]
    detail = (f"owner={psql.owner_role} attributes={','.join(attributes) or 'none'} "
              f"reachable={','.join(m['name'] for m in memberships) or 'none'}")
    if attributes or admin_memberships:
        report.skip('precondition',
                    'the privilege proofs are bounded by an administrative scratch owner',
                    detail + '; a privileged owner can undo any of them, so they are not '
                             'boundary proofs on this cluster')
    else:
        report.ok('precondition',
                  'the scratch owner holds no cluster administrative attribute or membership',
                  detail)



def apply_sql_files(psql: Psql, paths: list, report: Report, section: str) -> bool:
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            report.fail(section, f"apply {resolved.name}", "file does not exist")
            return False
        proc = psql.run_file(resolved)
        if proc.returncode != 0:
            report.fail(section, f"apply {resolved.name}",
                        (proc.stderr or proc.stdout).strip()[:400])
            return False
        report.ok(section, f"apply {resolved.name}")
    return True


def check_no_policy_rows(psql: Psql, report: Report) -> None:
    """The migration installs SCHEMA, never POLICY. This is the whole slice."""
    counts = psql.run(
        "SELECT (SELECT count(*) FROM ops.f01_policy_version) || ',' ||"
        " (SELECT count(*) FROM ops.f01_policy_current) || ',' ||"
        " (SELECT count(*) FROM ops.f01_corporate_artifact) || ',' ||"
        " (SELECT count(*) FROM ops.f01_field_state)")
    if counts.returncode != 0:
        report.fail("schema", "F01 relations exist after migration",
                    (counts.stderr or "").strip()[:400])
        return
    report.expect(counts.stdout.strip() == "0,0,0,0", "schema",
                  "the migration ships no policy, artifact or state row",
                  counts.stdout.strip())


def check_helper_prerequisites(psql: Psql, report: Report) -> None:
    """Read back the canonical authority-helper prerequisites the F01 schema DEPENDS on.

    None of this is installed, granted or repaired here. It is the boundary that
    0161_control_plane_authority_boundary.sql defines, restated as a readback so
    that a database missing a piece of it fails ONCE, early, with the name of the
    missing piece — instead of surfacing as "permission denied for function
    ops.authority_actor_slug" from inside a definer writer at fixture time, or as
    a wrong-principal FAIL that looks like an F01 defect and is not.

    The two derivations are deliberately asymmetric and both are pinned below:
    ops.f01_context_actor_slug() is SECURITY DEFINER (so it reaches the helper as
    the schema OWNER), ops.f01_require_authority_principal() is caller-rights (so
    it reaches the helper as the AUTHORITY LOGIN). session_user is unaffected by
    either, which is why they agree; but they need two different EXECUTE grants to
    get there, and only one of those two is visible from the F01 grant loop.
    """
    present = psql.scalar("SELECT to_regprocedure('ops.authority_actor_slug()') IS NOT NULL")
    if not report.expect(present == 't', 'prerequisite',
                         'the canonical helper ops.authority_actor_slug() is installed',
                         'absent: apply 0161_control_plane_authority_boundary.sql (or its '
                         'successor) before this gate; F01 refuses authority work without it '
                         'and never falls back to the schema owner'):
        return
    shape = json.loads(psql.scalar(
        "SELECT json_build_object("
        "'definer',(SELECT p.prosecdef FROM pg_proc p "
        "WHERE p.oid=to_regprocedure('ops.authority_actor_slug()')),"
        "'public',(SELECT count(*) FROM pg_proc p, LATERAL aclexplode("
        "coalesce(p.proacl,acldefault('f',p.proowner))) a "
        "WHERE p.oid='ops.authority_actor_slug()'::regprocedure AND a.grantee=0),"
        "'context_definer',(SELECT p.prosecdef FROM pg_proc p "
        "WHERE p.oid=to_regprocedure('ops.f01_context_actor_slug()')),"
        "'require_definer',(SELECT p.prosecdef FROM pg_proc p "
        "WHERE p.oid=to_regprocedure('ops.f01_require_authority_principal(text)')),"
        "'owner_execute',(SELECT has_function_privilege(pg_get_userbyid(p.proowner),"
        "'ops.authority_actor_slug()','EXECUTE') FROM pg_proc p "
        "WHERE p.oid=to_regprocedure('ops.f01_context_actor_slug()')),"
        "'definer_owner',(SELECT pg_get_userbyid(p.proowner) FROM pg_proc p "
        "WHERE p.oid=to_regprocedure('ops.f01_context_actor_slug()')))"))
    report.expect(shape['definer'] is True and shape['public'] == 0, 'prerequisite',
                  'ops.authority_actor_slug() is SECURITY DEFINER and PUBLIC holds no EXECUTE on it',
                  json.dumps(shape))
    # The reviewed shape, pinned so a later "tidy" cannot silently invert it. The
    # caller-rights half is not a defect: session_user is what both read, and a
    # gratuitous SECURITY DEFINER here would change which role needs the helper
    # grant without changing a single answer.
    report.expect(shape['context_definer'] is True and shape['require_definer'] is False,
                  'prerequisite',
                  'the definer/caller-rights split of the two F01 authority derivations is intact',
                  f"f01_context_actor_slug definer={shape['context_definer']}, "
                  f"f01_require_authority_principal definer={shape['require_definer']}")
    report.expect(shape['owner_execute'] in (True, 't'),
                  'prerequisite',
                  'the F01 definer owner can EXECUTE ops.authority_actor_slug()',
                  f"owner={shape['definer_owner']}; without this the SECURITY DEFINER path "
                  "raises permission denied inside every authority write, and the F01 grant "
                  "loop cannot supply it — the name does not match f01\\_%")
    for role in ('carr_authority_joe', 'carr_authority_dell'):
        allowed = psql.scalar(f"SELECT has_function_privilege({sql_literal(role)},"
                              "'ops.authority_actor_slug()','EXECUTE')")
        report.expect(allowed == 't', 'prerequisite',
                      f'{role} can EXECUTE ops.authority_actor_slug()',
                      'granted by 0161 to the group role carr_authority, of which this login '
                      'must be a member; the caller-rights f01_require_authority_principal '
                      'reaches the helper as this login and nothing else supplies the grant')
    for role in ('carr_writer', 'carr_reader'):
        allowed = psql.scalar(f"SELECT has_function_privilege({sql_literal(role)},"
                              "'ops.authority_actor_slug()','EXECUTE')")
        report.expect(allowed == 'f', 'prerequisite',
                      f'{role} cannot EXECUTE ops.authority_actor_slug()')


FIXTURE_ROLES = ('carr_reader','carr_writer','carr_authority_joe','carr_authority_dell')
# f01_insert_derivative_link is private for the same reason the idempotency pair
# is: it is reached only from inside a SECURITY DEFINER writer, where it runs as
# the owner whatever the caller is, so no runtime EXECUTE is needed and any
# runtime EXECUTE is a hole.
PRIVATE_HELPERS = {'f01_claim_idempotency','f01_settle_idempotency',
                   'f01_insert_derivative_link',
                   'f01_guard_direct_dml','f01_guard_append_only','f01_guard_no_truncate'}
WRITER_FUNCTIONS = {'f01_install_policy','f01_apply_observation','f01_record_artifact',
                    'f01_record_proposal','f01_register_derivative_link',
                    'f01_record_document','f01_record_hold',
                    'f01_record_deletion_evaluation'}
# THE READER'S EXCLUSION LIST IS TEN NAMES, NOT EIGHT. domain.sql's grant loop
# skips f01_replay_outcome and f01_require_authority_principal for carr_reader as
# well as the eight writers, and its own posture readback re-asserts all ten.
# A gate that expected EXECUTE=True on those two would fail a CORRECT schema
# before the fixture ever ran, so the expectation is stated once, here, from the
# grant model the schema actually installs:
#   * f01_replay_outcome is a SECURITY DEFINER door onto the settled result of
#     somebody else's mutation — a write outcome, and none of a reader's business.
#   * f01_require_authority_principal is an authority probe; the two definer
#     writers that need it reach it as the owner, so revoking it costs nothing.
# carr_writer KEEPS f01_register_derivative_link: the ordinary evidence principal
# is the trusted producer identity, and taking it away would leave the approved
# registration rule with nobody able to satisfy it. ("Approved" rather than
# "settled": that rule is a session approval with no canonical decision id, while
# Q129.D1 settles the retention registry and nothing about registration.)
READER_FORBIDDEN = WRITER_FUNCTIONS | {'f01_replay_outcome', 'f01_require_authority_principal'}
WRITER_FORBIDDEN = {'f01_install_policy', 'f01_record_hold', 'f01_require_authority_principal'}
# Every name the gate reasons about must actually be there. Without this, a
# migration that shipped one table and one function would satisfy an inventory
# asserted only to be non-empty, and every EXECUTE expectation below would be
# vacuously true for the functions that were never created.
REQUIRED_FUNCTIONS = WRITER_FUNCTIONS | PRIVATE_HELPERS | {
    'f01_read', 'f01_require_authority_principal', 'f01_replay_outcome',
    # The derivative-registration seam's read surface. Naming them here is what
    # stops a schema that shipped the table and the writer but not the coverage
    # answer from passing: without f01_derivative_coverage every deletion
    # evaluation would fail on a missing function rather than fail closed on an
    # unknown coverage state, which is a different — and much less honest —
    # reason to refuse.
    'f01_derivative_links', 'f01_derivative_coverage', 'f01_derivative_coverage_digest',
    'f01_stored_derivatives',
    # The reserved-kind list ops.f01_register_derivative_link refuses against,
    # named here for the same reason: a schema that shipped the writer without it
    # would not fail closed, it would fail with "function does not exist" on every
    # registration — and a schema where somebody replaced the guard with an inline
    # literal would keep working while quietly losing the one place a second
    # internal producer kind gets added.
    'f01_reserved_derivative_kinds'}
# Tables and partitioned tables are the DML surface. Views, matviews and foreign
# tables are included in the PRIVILEGE proof because they are the OWNER-RIGHTS
# BYPASS surface: an auto-updatable or rule-bearing view over an F01 table,
# owned by the schema owner and granted to PUBLIC or to a fixture role, writes
# the base table with the view owner's privileges and never reaches the
# f01_guard_direct_dml layer. They are not given the SELECT expectation, which
# is a contract about the base tables.
PROBED_KINDS = ('r', 'p')
GRANTED_KINDS = ('r', 'p', 'v', 'm', 'f')


def check_grants(psql: Psql, report: Report) -> None:
    kinds = ','.join(sql_literal(k) for k in GRANTED_KINDS)
    relations = json.loads(psql.scalar(
        "SELECT coalesce(json_agg(json_build_object('relation',c.oid::regclass::text,"
        "'kind',c.relkind) ORDER BY c.relname),'[]'::json) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE n.nspname='ops' AND c.relname LIKE 'f01\\_%' AND c.relkind IN ({kinds})"))
    report.expect(bool(relations), 'grants', 'F01 relations present for privilege proof',
                  ','.join(f"{r['relation']}[{r['kind']}]" for r in relations)[:300])
    for entry in relations:
        table, kind = entry['relation'], entry['kind']
        public = psql.scalar("SELECT (SELECT count(*) FROM pg_class c, "
            "LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
            f"WHERE c.oid={sql_literal(table)}::regclass AND a.grantee=0) + "
            "(SELECT count(*) FROM pg_attribute c, LATERAL aclexplode(c.attacl) a "
            f"WHERE c.attrelid={sql_literal(table)}::regclass "
            "AND c.attnum>0 AND NOT c.attisdropped AND a.grantee=0)")
        report.expect(public == '0', 'grants', f'{table} has no PUBLIC privileges', public)
        for role in FIXTURE_ROLES:
            effective = psql.scalar(f"SELECT has_table_privilege({sql_literal(role)},"
                f"{sql_literal(table)},'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR "
                f"has_any_column_privilege({sql_literal(role)},{sql_literal(table)},'INSERT,UPDATE,REFERENCES')")
            report.expect(effective == 'f', 'grants',
                          f'{role} has no effective mutation grant on {table}', f'relkind={kind}')
            if kind in PROBED_KINDS:
                readable = psql.scalar(f"SELECT has_table_privilege({sql_literal(role)},"
                                      f"{sql_literal(table)},'SELECT')")
                report.expect(readable == 't', 'grants', f'{role} can SELECT {table}')
    functions = json.loads(psql.scalar("SELECT coalesce(json_agg(json_build_object("
        "'signature',p.oid::regprocedure::text,'name',p.proname)), '[]'::json) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='ops' AND p.proname LIKE 'f01\\_%'"))
    missing = sorted(REQUIRED_FUNCTIONS - {fn['name'] for fn in functions})
    report.expect(not missing, 'grants',
                  'every contracted F01 writer, private helper and read surface exists',
                  ('missing: ' + ','.join(missing)) if missing else f'{len(functions)} function(s)')
    for role in FIXTURE_ROLES:
        schema_create = psql.scalar(f"SELECT has_schema_privilege({sql_literal(role)},'ops','CREATE')")
        report.expect(schema_create == 'f','grants',f'{role} cannot create objects in ops')
    for fn in functions:
        signature, name = fn['signature'], fn['name']
        public = psql.scalar("SELECT count(*) FROM pg_proc p, "
            "LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
            f"WHERE p.oid={sql_literal(signature)}::regprocedure AND a.grantee=0")
        report.expect(public == '0','grants',f'{signature} has no PUBLIC EXECUTE')
        for role in FIXTURE_ROLES:
            forbidden = (name in PRIVATE_HELPERS or name.startswith('f01_guard_')
                or role == 'carr_reader' and name in READER_FORBIDDEN
                or role == 'carr_writer' and name in WRITER_FORBIDDEN)
            actual = psql.scalar(f"SELECT has_function_privilege({sql_literal(role)},"
                                 f"{sql_literal(signature)},'EXECUTE')")
            report.expect(actual == ('f' if forbidden else 't'), 'grants',
                          f'{role} expected EXECUTE={not forbidden} on {signature}')


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def rollback_probe(psql: Psql, statement: str, role: str) -> str:
    """Always roll back, including an unexpected successful destructive statement."""
    session = PsqlSession(psql.dsn, psql.binary, 'negative-probe', user=role)
    try:
        session.execute("SET statement_timeout='10s'")
        session.execute('BEGIN')
        return session.execute(statement)
    finally:
        try:
            session.execute('ROLLBACK')
        finally:
            session.close()


def execute_grant_refusal(message: str, endpoint: str) -> bool:
    """The refusal must be the EXECUTE GRANT, and the message must say so.

    check_grants has already proved that carr_writer and carr_reader hold no
    EXECUTE on these endpoints, so this is the mechanism that must fire. Accepting
    'f01_authority_principal_refused:' here as well would make the two proofs
    interchangeable, and a loosened EXECUTE grant would then pass this section on
    the strength of the function body alone.
    """
    return bool(re.search(r'ERROR:\s+42501:', message)) and bool(
        re.search(r'permission denied for function (?:ops\.)?' + re.escape(endpoint)
                  + r'(?:\s|$)', message))


def probe_targets(psql: Psql) -> list:
    """Each F01 table with one ordinary column, for a well-attributed UPDATE.

    A column that does not exist raises 42703 in parse analysis, BEFORE the
    relation privilege check, and an identity or generated column raises 428C9 in
    the rewriter. Either would be reported as a privilege refusal that never
    happened, so the column is resolved from the catalog. 'tenant' is preferred
    because every F01 relation in the reviewed domain schema carries it.
    """
    kinds = ','.join(sql_literal(k) for k in PROBED_KINDS)
    return json.loads(psql.scalar(
        "SELECT coalesce(json_agg(json_build_object('table',c.relname,'column',"
        "(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 "
        "AND NOT a.attisdropped AND a.attidentity='' AND a.attgenerated='' "
        "ORDER BY (a.attname<>'tenant'), a.attnum LIMIT 1)) ORDER BY c.relname),'[]'::json) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE n.nspname='ops' AND c.relname LIKE 'f01\\_%' AND c.relkind IN ({kinds})"))


def check_runtime_direct_dml(psql: Psql, runtime_role: str, report: Report) -> None:
    for target in probe_targets(psql):
        table, column = target['table'], target['column']
        # Catalog identifiers are quoted, never interpolated as arbitrary SQL.
        relation = 'ops."' + table.replace('"','""') + '"'
        statements = [(f'INSERT INTO {relation} DEFAULT VALUES','INSERT'),
                      (f'DELETE FROM {relation}','DELETE'),
                      (f'TRUNCATE {relation}','TRUNCATE')]
        if column:
            quoted = '"' + column.replace('"','""') + '"'
            statements.insert(1, (f'UPDATE {relation} SET {quoted}={quoted}','UPDATE'))
        else:
            report.fail('direct_dml', f'{runtime_role} UPDATE probe on {table} has a target column',
                        'no ordinary updatable column; an UPDATE here could not be attributed '
                        'to privileges rather than to column resolution')
        for statement, label in statements:
            message = rollback_probe(psql, statement, runtime_role)
            refused = bool(re.search(r'ERROR:\s+42501:',message)) and (
                f'permission denied for table {table}' in message
                or 'f01_direct_dml_refused' in message or 'f01_truncate_refused' in message
                or 'f01_append_only' in message)
            report.expect(refused,'direct_dml',f'{runtime_role} cannot {label} {table}',message[:200])


def check_wrong_principal(psql: Psql, report: Report) -> None:
    forged = (CONTEXT_SQL + ";SELECT set_config('carr.actor_slug','joe',false),"
              "set_config('carr.actor_human','true',false),"
              "set_config('carr.actor_authorization_class','verified_partner',false);")
    for role in ('carr_writer', 'carr_reader'):
        # ops.f01_read(p_kind text, p_selector jsonb DEFAULT '{}') — the one-argument
        # call is the defaulted form, not a different function.
        readable = psql.run(CONTEXT_SQL + ";SELECT ops.f01_read('current_policy')", user=role)
        if not report.expect(readable.returncode == 0,'wrong_principal',
                             f'{role} reaches the ordinary read surface',readable.stderr[:200]):
            report.skip('wrong_principal', f'{role} forged-authority probes were not run',
                        'the ordinary read surface failed first, so the two endpoint refusals '
                        'below are unproved rather than passed')
            continue
        for endpoint in ('f01_install_policy', 'f01_record_hold'):
            message = rollback_probe(psql, forged + f"SELECT ops.{endpoint}(null,null,null,null)",role)
            report.expect(execute_grant_refusal(message, endpoint), 'wrong_principal',
                          f'{role} is refused {endpoint} by the EXECUTE grant despite forged authority',
                          message[:220])
        if role == 'carr_reader':
            # A READ-ONLY PRINCIPAL IS NOT A PRODUCER. The registration writer is
            # the one new write surface, and carr_writer legitimately holds it —
            # it IS the trusted producer identity — so only the reader is probed
            # here. Three arguments, not four: this writer takes envelope, key
            # and request digest.
            message = rollback_probe(
                psql, forged + "SELECT ops.f01_register_derivative_link(null,null,null)", role)
            report.expect(
                execute_grant_refusal(message, 'f01_register_derivative_link'), 'wrong_principal',
                'carr_reader is refused f01_register_derivative_link by the EXECUTE grant '
                'despite forged authority', message[:220])
    for role, actor in (('carr_authority_joe', 'joe'), ('carr_authority_dell', 'dell')):
        # Caller flags must not override authenticated session_user authority.
        attempt = psql.run("SELECT set_config('carr.acting_actor_slug','mallory',false),"
            "set_config('carr.actor_human','false',false),"
            "set_config('carr.actor_authorization_class','unsponsored_agent',false);"
            "SELECT ops.f01_require_authority_principal('gate-probe')", user=role)
        # An empty stdout on a zero exit is a failed probe, never an index error
        # that replaces the whole report with a traceback.
        derived = (attempt.stdout.strip().splitlines() or [''])[-1]
        report.expect(attempt.returncode == 0 and derived == actor,
                      'wrong_principal', f'{role} derives {actor} despite forged caller flags',
                      (attempt.stderr or attempt.stdout).strip()[:200])


# The jsonb parameters of ops.f01_apply_observation, by position:
#   5 p_state  6 p_transition  7 p_event  8 p_receipt  9 p_reconciliation
#   12 p_diagnostics (present only in the 13-argument form)
RACE_JSONB_POSITIONS = frozenset((5, 6, 7, 8, 9, 12))


def race_statement(args: list) -> str:
    """Render one emitted race request as SQL.

    THE TRANSPORT SHAPE IS RAW JSON, AND ONLY RAW JSON. The fixture emits
    F01_RACE_REQUEST as a JSON array of VALUES — a plain string for each text
    parameter, a JSON object for each jsonb parameter, JSON null for an absent
    one — and this function does every bit of the quoting. It has to be this way
    round: the quoting is the injection boundary, it is adversarially tested
    here, and a fixture that pre-rendered SQL fragments would have that boundary
    applied to its own quotes a second time.

    So a pre-rendered argument is refused BY NAME rather than falling through the
    'accept' comparison as an unrecognisable transition, because "race requests
    must accept transitions" is a maddening thing to be told about a request that
    does.
    """
    if len(args) not in (12, 13):
        raise GateRefusal(f'race requests must have 12 or 13 arguments, got {len(args)}')
    for i, value in enumerate(args):
        if isinstance(value, str) and (value.startswith("'") or value.endswith('::jsonb')):
            raise GateRefusal(
                f'race argument {i} arrives pre-rendered as a SQL fragment ({value[:40]!r}). '
                'The fixture must emit raw JSON values; this gate does the quoting')
        if i in RACE_JSONB_POSITIONS:
            if value is not None and not isinstance(value, (dict, list)):
                raise GateRefusal(
                    f'race argument {i} is a jsonb parameter and must arrive as a JSON '
                    f'object, array or null, not {type(value).__name__}')
        elif value is not None and not isinstance(value, str):
            raise GateRefusal(f'non-text race identity argument at position {i}')
    if args[0] != 'accept':
        raise GateRefusal(f'race requests must be accept transitions, got {args[0]!r}')
    values = []
    for i, value in enumerate(args):
        if value is None:
            values.append('NULL')
        elif i in RACE_JSONB_POSITIONS:
            values.append(sql_literal(json.dumps(value, ensure_ascii=True)) + '::jsonb')
        else:
            values.append(sql_literal(value))
    return 'SELECT ops.f01_apply_observation(' + ','.join(values) + ')'


def exact_stale_state_refusal(output: str) -> bool:
    return bool(re.search(r'ERROR:\s+40001:\s+f01_stale_current_state:', output))


def check_concurrency(dsn: str, binary: str, owner_role: str, requests: dict, report: Report) -> None:
    """Two accepted transitions from the same prior state; precisely one wins."""
    args_a, args_b = requests['race_a'], requests['race_b']
    sql_a, sql_b = race_statement(args_a), race_statement(args_b)
    if args_a[1:5] != args_b[1:5] or args_a[10] == args_b[10]:
        raise GateRefusal('race inputs must share identity and prior state with distinct keys')
    entity, field = map(sql_literal, args_a[1:3])
    read_state = f"SELECT ops.f01_current_field_state({entity},{field})->>'state_digest'"
    counts_sql = "SELECT json_build_array(" + ','.join(
        f"(SELECT count(*) FROM ops.{table} WHERE entity={entity} AND field={field})"
        for table in ('f01_field_event','f01_state_transition','f01_mutation_receipt',
                      'f01_reconciliation_item','f01_field_state')) + ')'
    a = PsqlSession(dsn, binary, 'A', user='carr_writer')
    b = PsqlSession(dsn, binary, 'B', user='carr_writer')
    try:
        # SESSION B IS GIVEN ROOM TO WAIT. B blocks on the winner's field lock for
        # as long as the monitor loop takes to observe the wait (up to 5s) plus
        # A's commit. At 10s a slow scratch machine can cancel B with 57014
        # instead of letting it refuse with 40001 — which would fail
        # exact_stale_state_refusal and report a CORRECT implementation as broken.
        # 45s stays comfortably under PsqlSession.READ_TIMEOUT, so a genuinely
        # stuck B is still a bounded, named failure rather than a hang.
        for session, timeout in ((a, '10s'), (b, '45s')):
            session.execute(CONTEXT_SQL)
            session.execute(f"SET statement_timeout='{timeout}'")
            session.execute('BEGIN')
        seen_a, seen_b = a.execute(read_state).strip(), b.execute(read_state).strip()
        if not report.expect(seen_a == seen_b == args_a[4], 'concurrency',
                             'both sessions read the fixture prior state', seen_a):
            return
        before_counts = json.loads(a.execute(counts_sql))
        advance = a.execute(sql_a)
        try:
            result = json.loads(advance)
        except ValueError:
            report.fail('concurrency', 'first accepted transition', advance[:300])
            return
        winner_digest = result.get('readback', {}).get('state_digest')
        if not report.expect(result.get('outcome') == 'accepted' and bool(winner_digest)
                             and winner_digest != seen_a, 'concurrency',
                             'winner accepts and changes the state digest', advance[:200]):
            return
        pid_b = int(b.execute('SELECT pg_backend_pid()').strip())
        b.send(sql_b)
        # OBSERVE AS THE RACING ROLE. pg_stat_activity nulls state, wait_event_type
        # and query for backends whose role the caller does not hold, so an
        # owner-role monitor reads NULL for a carr_writer backend, concludes
        # 'not blocked', and fails a CORRECT implementation. The pressure that
        # creates — make the owner a superuser — would void the whole privilege
        # section. carr_writer observing carr_writer needs no privilege at all.
        # pg_blocking_pids is not stats-restricted and corroborates independently.
        monitor = Psql(dsn, binary, owner_role)
        observe = ("SELECT json_build_object("
                   "'present',count(*)>0,"
                   "'visible',coalesce(bool_or(state IS NOT NULL),false),"
                   "'lock_wait',coalesce(bool_or(wait_event_type='Lock'),false),"
                   "'blocked_by',coalesce(bool_or(cardinality(pg_blocking_pids(pid))>0),false)) "
                   f"FROM pg_stat_activity WHERE pid={pid_b}")
        deadline = time.monotonic() + 5
        blocked = False
        observation = {}
        while time.monotonic() < deadline:
            observation = json.loads(monitor.scalar(observe, user='carr_writer'))
            blocked = bool(observation.get('lock_wait') or observation.get('blocked_by'))
            if blocked:
                break
            time.sleep(0.05)
        unobservable = bool(observation.get('present')) and not observation.get('visible')
        if not report.expect(blocked, 'concurrency', 'second session visibly waits on the winner lock',
                             'the racing backend is not observable by the monitor principal, so '
                             'waiting could be neither seen nor ruled out'
                             if unobservable else json.dumps(observation)):
            a.execute('ROLLBACK')
            b.collect()
            b.execute('ROLLBACK')
            return
        a.execute('COMMIT')
        loser = b.collect()
        report.expect(exact_stale_state_refusal(loser), 'concurrency',
                      'loser refuses specifically with stale-state SQLSTATE 40001', loser[:300])
        b.execute('ROLLBACK')
        report.expect(a.execute(read_state).strip() == winner_digest, 'concurrency',
                      'loser preserves the exact winner state digest')
        # The counts are scoped to the raced entity/field, and the assertion is on
        # the DELTA, so the honest label is "no NEW reconciliation item" — this
        # says nothing about items the fixture may have recorded earlier for this
        # field, and it should not: the race writes none, which is the claim.
        # The current-state count is asserted as a before-value in its own right
        # rather than silently overridden into the expectation.
        report.expect(before_counts[4] == 1, 'concurrency',
                      'the fixture established exactly one current-state row for the raced field',
                      json.dumps(before_counts))
        expected_counts = [before_counts[i] + delta for i, delta in enumerate((1,1,1,0,0))]
        report.expect(json.loads(a.execute(counts_sql)) == expected_counts, 'concurrency',
                      'exactly one new event, transition and receipt; no new reconciliation item; '
                      'the current-state row is replaced in place',
                      f'{before_counts} -> {expected_counts}')
        replay = json.loads(a.execute(sql_a))
        report.expect(replay == result, 'concurrency', 'winner request replays exact original result')
        report.expect(json.loads(a.execute(counts_sql)) == expected_counts,
                      'concurrency', 'idempotent replay changes no record counts')
        report.expect(a.execute(read_state).strip() == winner_digest, 'concurrency',
                      'idempotent replay preserves the exact winner digest')
    finally:
        a.close()
        b.close()


def check_canonical_agreement(psql: Psql, repo_root: Path, node_binary: str,
                              report: Report) -> None:
    """Byte-for-byte, and the database never sees the expectation first."""
    try:
        expected = node_canonical(CANONICAL_CASES, repo_root, node_binary)
    # A missing node raises FileNotFoundError, which is an OSError and NOT a
    # SubprocessError; unparsable stdout raises ValueError. Neither is a reason
    # to end the run in a traceback instead of a FAILED check.
    except (GateRefusal, OSError, ValueError, subprocess.SubprocessError) as error:
        report.fail("canonical", "compute expected bytes in Node",
                    f'{type(error).__name__}: {error}'[:300])
        return

    mismatches = 0
    for case in expected:
        raw = case["raw"]
        if "$f01$" in raw:
            report.fail("canonical", "case is not dollar-quotable", raw[:80])
            mismatches += 1
            continue
        got = psql.run(
            f"SELECT ops.f01_canonical_json($f01${raw}$f01$::jsonb) || E'\\n' || "
            f"ops.f01_digest_jsonb($f01${raw}$f01$::jsonb)")
        if got.returncode != 0:
            report.fail("canonical", f"PostgreSQL canonicalised {raw[:60]}",
                        (got.stderr or "").strip()[:200])
            mismatches += 1
            continue
        parts = got.stdout.strip().split("\n")
        pg_canonical, pg_digest = parts[0], parts[-1]
        if pg_canonical != case["canonical"]:
            report.fail("canonical", f"bytes disagree for {raw[:50]}",
                        f"node={case['canonical']!r} pg={pg_canonical!r}")
            mismatches += 1
        elif pg_digest != case["digest"]:
            report.fail("canonical", f"digests disagree for {raw[:50]}",
                        f"node={case['digest']} pg={pg_digest}")
            mismatches += 1
    report.expect(mismatches == 0, "canonical",
                  f"all {len(expected)} Node/PostgreSQL canonical cases agree byte for byte",
                  f"{mismatches} mismatch(es)")


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------

def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V5-F01 local disposable-PostgreSQL gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            This gate never discovers a connection, never reads a credential and
            never runs against anything but a local f01_gate_* scratch database.
            The parent supplies the exact migration paths and the exact DSN.
        """),
    )
    parser.add_argument("--dsn", required=True,
                        help="explicit postgresql:// URI for a LOCAL DISPOSABLE database")
    parser.add_argument("--confirm-disposable", required=True,
                        help="repeat the scratch database name exactly")
    parser.add_argument("--migration", action="append", default=[],
                        help="a migration file to apply, in order; repeatable")
    parser.add_argument("--domain-sql", default=None,
                        help="apply domain.sql directly (draft convenience, not the "
                             "acceptance path)")
    parser.add_argument("--fixture", default="mcp-server/test/record-source-authority-postgres.sql",
                        help="the SQL fixture suite")
    parser.add_argument("--repo-root", default=".", help="repository root, for the Node canonicaliser")
    parser.add_argument("--owner-role", required=True,
                        help="explicit preprovisioned scratch database owner login")
    parser.add_argument("--fixture-role", default=None,
                        help="an EXISTING, separate, preprovisioned superuser bootstrap login, "
                             "used for the single psql -f of the SQL fixture and for nothing "
                             "else. Omit it and the fixture is applied as the owner, which then "
                             "has to be a superuser and is recorded as a skip")
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--node", default="node")
    parser.add_argument("--skip-concurrency", action="store_true")
    return parser.parse_args(argv)


def resolve_input(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else repo_root / path).resolve()


def main(argv: list) -> int:
    args = parse_args(argv)
    report = Report()

    try:
        db_name = assert_disposable_dsn(args.dsn, args.confirm_disposable)
    except GateRefusal as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    # Resolve once and execute the RESOLVED path. connection_env preserves PATH,
    # so checking one string and running another leaves the binary unpinned for
    # the rest of the run.
    for attribute in ('psql', 'node'):
        resolved = shutil.which(getattr(args, attribute))
        if resolved is None:
            print(f"REFUSED: {getattr(args, attribute)} is not on PATH", file=sys.stderr)
            return 2
        setattr(args, attribute, resolved)
    if args.migration and args.domain_sql:
        print("REFUSED: migration acceptance and draft domain modes are mutually exclusive", file=sys.stderr)
        return 2
    if not args.migration and not args.domain_sql:
        print("REFUSED: pass --migration (preferred) or --domain-sql; this gate supplies neither",
              file=sys.stderr)
        return 2

    repo_root = Path(args.repo_root).expanduser().resolve()
    psql = Psql(args.dsn, args.psql, args.owner_role)

    print(f"V5-F01 local gate — disposable database {db_name}")
    print("  no credential is read, no connection is discovered, no provider is called")
    print()

    # Every preflight statement is inside the guarded block: a server error here
    # is a REFUSAL with a reason, not a traceback in place of a verdict.
    try:
        verify_connections(psql, db_name)
        version = int(psql.scalar("SELECT current_setting('server_version_num')::int"))
        if version < 130000:
            raise GateRefusal('PostgreSQL 13 or later is required')
        report.ok('connect', 'all real scratch principals and server identity verified before writes')
        owner_precondition(psql, report)
        if args.fixture_role:
            verify_fixture_principal(psql, db_name, args.fixture_role, report)
        before_cluster = cluster_inventory(psql)
    except (GateRefusal, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"REFUSED: connection/principal preflight: {error}", file=sys.stderr)
        return 2

    try:
        result = run_verified(args, repo_root, psql, report)
    finally:
        # The readback must never replace the outcome it is reporting on. If the
        # server is gone, that is a FAILED check with a name.
        try:
            unchanged = report.expect(cluster_inventory(psql) == before_cluster, 'cluster',
                'role, membership, database, extension and language inventory unchanged at readback',
                'observation only; reviewed input files remain a precondition')
        except Exception as error:
            report.fail('cluster', 'read the cluster inventory back',
                        f'{type(error).__name__}: {error}'[:300])
            unchanged = False
    return result if unchanged else 1


def cluster_inventory(psql: Psql) -> str:
    return psql.scalar("SELECT json_build_object("
        "'roles',(SELECT json_agg(row_to_json(r) ORDER BY r.rolname) FROM "
        "(SELECT oid,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_roles) r),"
        "'memberships',(SELECT json_agg(row_to_json(m) ORDER BY m.roleid,m.member,m.grantor) "
        "FROM pg_auth_members m),"
        "'databases',(SELECT json_agg(row_to_json(d) ORDER BY d.datname) FROM "
        "(SELECT oid,datname,datdba,datistemplate,datallowconn,datconnlimit,datacl FROM pg_database) d),"
        # The F01 slice installs no extension and no language. An installed
        # extension is where a persistent effect capability — dblink, a foreign
        # data wrapper, an untrusted PL — would actually show up.
        "'extensions',(SELECT json_agg(row_to_json(e) ORDER BY e.extname) FROM "
        "(SELECT extname,extversion,extnamespace FROM pg_extension) e),"
        "'languages',(SELECT json_agg(row_to_json(l) ORDER BY l.lanname) FROM "
        "(SELECT lanname,lanpltrusted,lanowner FROM pg_language) l))")


def guarded(report: Report, section: str, label: str, check, *args) -> None:
    """A check that crashes is a FAILED check.

    Every section below talks to a live server. A dropped connection, a psql
    timeout or a catalog query the migration made unanswerable must end as a
    named row and a summary the parent can read, never as a traceback that
    replaces the verdict for every other section too.
    """
    try:
        check(*args)
    except Exception as error:
        report.fail(section, label, f'{type(error).__name__}: {error}'[:300])


def run_verified(args: argparse.Namespace, repo_root: Path, psql: Psql, report: Report) -> int:
    legacy_before = psql.scalar(LEGACY_FINGERPRINT_SQL)
    rows_before = legacy_rowcounts(psql)

    if args.migration:
        if not apply_sql_files(psql, [resolve_input(repo_root, p) for p in args.migration], report, "migration"):
            print()
            print("REFUSED: a migration failed to apply; nothing further was run.")
            print(report.summary())
            return 1
    if args.domain_sql:
        if not apply_sql_files(psql, [resolve_input(repo_root, args.domain_sql)], report, "domain"):
            print()
            print(report.summary())
            return 1

    guarded(report, "schema", "the schema section ran to completion",
            check_no_policy_rows, psql, report)
    guarded(report, "prerequisite", "the helper-prerequisite section ran to completion",
            check_helper_prerequisites, psql, report)
    guarded(report, "grants", "the grants section ran to completion",
            check_grants, psql, report)
    guarded(report, "canonical", "the canonical section ran to completion",
            check_canonical_agreement, psql, repo_root, args.node, report)

    if report.failures:
        print(report.summary())
        return 1
    race_requests = None
    fixture = resolve_input(repo_root, args.fixture)
    if not fixture.exists():
        report.fail("fixture", "locate the SQL fixture suite", str(fixture))
    else:
        # THE ONE CALL THE FIXTURE PRINCIPAL MAKES. Everything before and after
        # this line runs as the owner or as a carr_* principal.
        ops_before = ops_fingerprint(psql, 'before')
        proc = psql.run_file(fixture, user=args.fixture_role)
        ops_after = ops_fingerprint(psql, 'after')
        if proc.returncode == 0:
            tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-12:]
            report.ok("fixture", "the SQL fixture suite passed", " | ".join(tail)[:400])
            for line in proc.stdout.splitlines():
                if line.startswith('F01_RACE_REQUEST='):
                    race_requests = json.loads(line.removeprefix('F01_RACE_REQUEST='))
        else:
            report.fail("fixture", "the SQL fixture suite failed",
                        (proc.stderr or proc.stdout).strip()[-800:])
        # Section 14 of the fixture legitimately drops two constraints and
        # disables a guard trigger inside one transaction, and restores them
        # before it commits. This says they came back, and that nothing else in
        # the ops schema was reassigned, re-granted or left disabled — the exact
        # damage a superuser applier could do and an owner applier could not.
        report.expect(ops_before == ops_after, 'fixture',
                      'ops ownership, ACLs, triggers, constraints and schema ACL are identical '
                      'before and after the fixture applies',
                      f'applied by {args.fixture_role or psql.owner_role}; detection, not '
                      'containment — the fixture file remains a reviewed input')

    guarded(report, "wrong_principal", "the wrong-principal section ran to completion",
            check_wrong_principal, psql, report)
    for role in FIXTURE_ROLES:
        guarded(report, "direct_dml", f"the direct-DML section ran to completion for {role}",
                check_runtime_direct_dml, psql, role, report)
    if report.failures:
        print(report.summary())
        return 1

    if args.skip_concurrency:
        report.skip("concurrency", "race proofs", "--skip-concurrency was passed")
    else:
        try:
            if race_requests is None:
                raise GateRefusal('fixture did not emit F01_RACE_REQUEST payloads')
            check_concurrency(args.dsn, args.psql, args.owner_role, race_requests, report)
        except Exception as error:  # a race harness failure is a gate failure
            report.fail("concurrency", "race harness", str(error)[:300])

    legacy_after = psql.scalar(LEGACY_FINGERPRINT_SQL)
    rows_after = legacy_rowcounts(psql)
    # The label names exactly what the fingerprint captures. It does not capture
    # indexes, defaults, storage parameters, or row contents beyond the counts
    # asserted separately below.
    report.expect(legacy_before == legacy_after, "legacy",
                  "public.record_source and public.document columns, constraints, table and "
                  "column ACLs, RLS flags, triggers and rules are unchanged")
    report.expect(rows_before == rows_after, "legacy",
                  "legacy row counts are unchanged", f"{rows_before} -> {rows_after}")

    # Scan EVERY ops function, not only f01_*: a helper carrying an effect would
    # simply be named something else. Language is included because prosrc says
    # nothing about a C or untrusted-PL function. This is a keyword scan and the
    # label says so; the before/after extension and language inventory is where a
    # persistent capability install is actually detected.
    external = json.loads(psql.scalar(
        "SELECT coalesce(json_agg(p.oid::regprocedure::text "
        "ORDER BY p.oid::regprocedure::text),'[]'::json) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "JOIN pg_language l ON l.oid = p.prolang WHERE n.nspname='ops' "
        "AND (l.lanname NOT IN ('sql','plpgsql') "
        "OR p.prosrc ILIKE '%dblink%' OR p.prosrc ILIKE '%pg_read_file%' "
        "OR p.prosrc ILIKE '%COPY %FROM PROGRAM%' OR p.prosrc ILIKE '%http%')"))
    report.expect(not external, "effects",
                  "keyword scan of every ops function finds no listed effect indicator and no "
                  "non-PL language (a heuristic, not proof of absence)",
                  ','.join(external)[:300] or "no match")

    print()
    print(report.summary())
    if report.failures:
        print("GATE FAILED")
        return 1
    if report.skips:
        # NOT 3. A draft domain proof and an acceptance run with the strongest
        # proof omitted are different outcomes, and a parent that cannot tell
        # them apart will eventually accept the second one for the first.
        print("GATE PASSED WITH SKIPS — a skip is not acceptance; each must be resolved")
        return 4
    print("DRAFT DOMAIN PROOF PASSED — predecessor migration integration is unverified"
          if args.domain_sql else "GATE PASSED")
    return 3 if args.domain_sql else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
