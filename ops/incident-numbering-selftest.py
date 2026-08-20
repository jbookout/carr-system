#!/usr/bin/env python3
"""Selftest for incident ref numbering — the day prefix and the sequence must
come from ONE clock.

WHY THIS EXISTS. Until 2026-08-18 tools/ops-record.py's _next_incident_ref read
the day twice. The sequence counted rows matching

    ref like 'INC-' || to_char(now(), 'YYYYMMDD') || '-%'

which Postgres evaluates in the SERVER's timezone, while the ref it returned was
formatted from datetime.now(timezone.utc) — the CLIENT's. The two agree only
when the server runs UTC. Production was correct by luck: Neon is UTC, so both
clocks named the same day and nothing ever collided.

Against a local Postgres 17 inheriting the Mac's America/Chicago zone the split
is plainly visible — reproduced 2026-08-18 19:22 CDT, and again while this test
was written:

    select to_char(now(), 'YYYYMMDD'),               -->  20260818
           to_char(now() at time zone 'UTC','YYYYMMDD')  -->  20260819

The count matched prefix INC-20260818- (no rows, so seq 01) while the ref
written said INC-20260819-01. Every incident opened in that five-hour window is
numbered 01, and the second one dies on incident_ref_key:

    psycopg.errors.UniqueViolation: duplicate key value violates unique
    constraint "incident_ref_key"

ops/program3-incident-gate.py fails exactly this way. mcp-server/src/trace.js
carried the identical split (server-zone count, `new Date()` label) and is fixed
alongside, because the two writers deliberately share ONE numbering space per
day — counting in one space while labelling in two would be worse than the
original defect.

WHAT IS ASSERTED. Tier 1 needs no database: a fake cursor models the one thing
that matters about a non-UTC Postgres — it answers `now()` in its own zone and
`now() at time zone 'UTC'` in UTC — so the shipped query is run against a server
sitting on the wrong side of the boundary. The old two-clock formula is run
against that same fake and MUST collide; a regression test that cannot fail
against the defect it names is decoration.

Tier 2 (opt-in, CARR_INCIDENT_NUMBERING_DSN) does it for real: a Postgres
session pinned to America/Chicago, two incidents actually inserted under a real
unique constraint, inside a transaction that is always rolled back.

RUN IT:
    python3 ops/incident-numbering-selftest.py                       # tier 1
    CARR_INCIDENT_NUMBERING_DSN=postgresql://.../postgres \\
        python3 ops/incident-numbering-selftest.py                   # + tier 2
"""
import importlib.util
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "tools", "ops-record.py")
TRACE_JS = os.path.join(ROOT, "mcp-server", "src", "trace.js")

spec = importlib.util.spec_from_file_location("ops_record", TOOL)
assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
opsrec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opsrec)

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ──────────────────────────────────────────────────────────────────────────
# A Postgres standing in one timezone, small enough to reason about.
#
# It models the ONE behaviour this defect turns on: to_char(now(), ...) reads
# the day in the server's own zone, and to_char(now() at time zone 'UTC', ...)
# reads it in UTC. Everything else — the max(substring(...)) sequence, the ref
# unique constraint — is implemented literally, so the shipped query and the
# old one can both be run against the same server and compared.
# ──────────────────────────────────────────────────────────────────────────
class DuplicateRef(Exception):
    """What incident_ref_key raises, in the shape this test cares about."""


class FakeServer:
    def __init__(self, local_day: str, utc_day: str):
        self.local_day = local_day   # what a bare to_char(now(), ...) answers
        self.utc_day = utc_day       # what `now() at time zone 'UTC'` answers
        self.refs: list[str] = []

    def insert(self, ref: str) -> None:
        if ref in self.refs:
            raise DuplicateRef(
                f'duplicate key value violates unique constraint '
                f'"incident_ref_key" (ref={ref})')
        self.refs.append(ref)

    def cursor(self):
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, server: FakeServer):
        self.server = server
        self._row = None
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        day = self.server.utc_day if "at time zone 'utc'" in sql.lower() \
            else self.server.local_day
        seq = 1 + max(
            (int(r.rsplit("-", 1)[1]) for r in self.server.refs
             if r.startswith(f"INC-{day}-")),
            default=0)
        # The shipped query selects the day alongside the sequence; the old one
        # selected the sequence alone and left the caller to name the day.
        self._row = (day, seq) if re.search(r"to_char\(.*\bas day", sql, re.S) \
            else (seq,)

    def fetchone(self):
        return self._row


# The formula as it stood before 2026-08-18, kept verbatim so the fake server
# can be asked whether this test would actually have caught the defect.
OLD_QUERY = """select coalesce(max(substring(ref from '[0-9]+$')::int), 0) + 1
                 from ops.incident
                where ref like 'INC-' || to_char(now(), 'YYYYMMDD') || '-%'"""


def old_next_incident_ref(cur, client_utc_day: str) -> str:
    cur.execute(OLD_QUERY)
    return f"INC-{client_utc_day}-{cur.fetchone()[0]:02d}"


# 19:22 America/Chicago on 2026-08-18 — the reproduction. The server is still
# on the 18th; every UTC clock in the system has already turned over to the
# 19th, and stays that way for five hours.
CENTRAL_DAY, UTC_DAY = "20260818", "20260819"


@case("the ref carries the day the SERVER counted under, not a date this process formatted")
def _(assert_):
    # A day no client clock can produce: if it reaches the ref, the label and
    # the sequence came out of the same answer.
    server = FakeServer(local_day="19991230", utc_day="19991231")
    cur = server.cursor()
    ref = opsrec._next_incident_ref(cur)
    assert_(ref == "INC-19991231-01", f"expected INC-19991231-01, got {ref}")
    assert_(any("at time zone 'UTC'" in s for s in cur.statements),
            "the query must pin the day to UTC, or the numbering space moves "
            "with whatever zone the cluster happens to inherit")


@case("two incidents opened against a non-UTC server get DISTINCT refs")
def _(assert_):
    server = FakeServer(local_day=CENTRAL_DAY, utc_day=UTC_DAY)
    refs = []
    for _i in range(2):
        ref = opsrec._next_incident_ref(server.cursor())
        server.insert(ref)          # the real insert, under the real constraint
        refs.append(ref)
    assert_(refs == [f"INC-{UTC_DAY}-01", f"INC-{UTC_DAY}-02"],
            f"the sequence must advance within the day it counted: {refs}")
    assert_(len(set(refs)) == 2, f"two incidents shared a ref: {refs}")


@case("the OLD two-clock formula collides against that same server — this test can fail")
def _(assert_):
    # Without this, the two cases above prove only that the current code agrees
    # with itself. Here the defect is run against the same fake, and the second
    # incident must die exactly as it does in production-shaped runs.
    server = FakeServer(local_day=CENTRAL_DAY, utc_day=UTC_DAY)
    first = old_next_incident_ref(server.cursor(), client_utc_day=UTC_DAY)
    server.insert(first)
    second = old_next_incident_ref(server.cursor(), client_utc_day=UTC_DAY)
    assert_(first == second == f"INC-{UTC_DAY}-01",
            f"the old formula should number both 01: {first!r}, {second!r}")
    try:
        server.insert(second)
    except DuplicateRef:
        return
    assert_(False, "the old formula must collide on the second incident — if it "
                   "does not, this fake no longer models the failure")


@case("both writers ship the same UTC-pinned shape, so they share one numbering space")
def _(assert_):
    # tools/ops-record.py and mcp-server/src/trace.js open incidents into the
    # same table and deliberately count in the same space (a Cloudflare Worker
    # cannot invoke Python, so the shape is what is shared rather than the
    # process). If one pins UTC and the other reads the server's zone, they
    # count in one space and label in two.
    for path in (TOOL, TRACE_JS):
        # Comment lines are dropped first: both files EXPLAIN the old shape in
        # prose right above the fixed query, and prose is not what ships.
        code = [ln for ln in open(path, encoding="utf-8").read().splitlines()
                if not ln.lstrip().startswith(("#", "//", "*"))]
        # Only the numbering query matters; the other now() calls (detected_at,
        # observed_at, expires_at) are timestamps, not day labels.
        numbering = [ln for ln in code if "'YYYYMMDD'" in ln]
        assert_(numbering, f"{os.path.basename(path)} has no day-prefix query at all")
        for ln in numbering:
            assert_("at time zone 'UTC'" in ln,
                    f"{os.path.basename(path)}: day prefix read in the server's "
                    f"zone: {ln.strip()}")
        bare = [ln for ln in code if "to_char(now()," in ln.replace(" ", "")]
        assert_(not bare,
                f"{os.path.basename(path)}: a bare to_char(now(), ...) is the "
                f"server-zone read this fix removed: {bare}")


# ──────────────────────────────────────────────────────────────────────────
# TIER 2 — the same thing against a real Postgres in a real non-UTC session.
#
# Opt-in, and pointed at a throwaway cluster on purpose: it inserts incident
# rows. Everything happens inside one transaction that is ALWAYS rolled back,
# in a schema named for this run, so even the table it creates does not
# survive. ops-record.py's own connections are autocommit (see connect()); the
# transaction here belongs to the test, not to the code under test.
# ──────────────────────────────────────────────────────────────────────────
PROBE_TZ = "America/Chicago"


class SchemaRedirect:
    """The shipped cursor, with ops.incident pointed at this run's probe table.

    The SQL under test is byte-for-byte what ships, apart from the table name —
    which is the point: the probe table can be created and thrown away, while
    the day derivation, the sequence and the unique constraint are the real
    ones Postgres evaluates.
    """

    def __init__(self, cur, schema: str):
        self._cur, self._schema = cur, schema

    def execute(self, sql, params=None):
        return self._cur.execute(sql.replace("ops.incident", f"{self._schema}.incident"),
                                 params)

    def fetchone(self):
        return self._cur.fetchone()


def tier2(dsn: str) -> None:
    import psycopg

    schema = "incnum_probe_" + uuid.uuid4().hex[:8]

    def case2(name):
        """Register a tier-2 case, each inside its OWN savepoint.

        A failing statement poisons a Postgres transaction — every later
        command comes back InFailedSqlTransaction — so without this the first
        genuine failure would be reported once and then mask every case after
        it as a transaction error. The savepoint is rolled back either way, so
        no case inherits another's rows.
        """
        def deco(fn):
            def wrapped(assert_, _fn=fn):
                cur.execute("savepoint case_probe")
                try:
                    _fn(assert_)
                finally:
                    cur.execute("rollback to savepoint case_probe")
            CASES.append((name, wrapped))
            return wrapped
        return deco

    with psycopg.connect(dsn) as conn:          # NOT autocommit: rollback is the cleanup
        with conn.cursor() as cur:
            cur.execute(f"set local time zone '{PROBE_TZ}'")
            cur.execute(f"create schema {schema}")
            cur.execute(f"""create table {schema}.incident (
                                id   bigserial primary key,
                                ref  text not null unique)""")

            @case2("[tier 2] a real non-UTC session reads a different day than UTC — or there "
                   "is nothing here to test")
            def _(assert_, cur=cur):
                cur.execute("""select to_char(now(), 'YYYYMMDD'),
                                      to_char(now() at time zone 'UTC', 'YYYYMMDD'),
                                      current_setting('TimeZone')""")
                server_day, utc_day, tz = cur.fetchone()
                assert_(tz == PROBE_TZ, f"the session must be pinned to {PROBE_TZ}, got {tz}")
                if server_day == utc_day:
                    print(f"    (note: {PROBE_TZ} and UTC are on the same day right now "
                          f"({utc_day}); the numbering assertions below still hold, but the "
                          f"divergence itself is only visible between 19:00 CDT and midnight)")

            @case2("[tier 2] two incidents inserted through the shipped query get distinct refs")
            def _(assert_, cur=cur, schema=schema):
                redirected = SchemaRedirect(cur, schema)
                refs = []
                for _i in range(2):
                    ref = opsrec._next_incident_ref(redirected)
                    cur.execute(f"insert into {schema}.incident (ref) values (%s)", (ref,))
                    refs.append(ref)
                assert_(len(set(refs)) == 2,
                        f"the second insert would have raised incident_ref_key: {refs}")

                cur.execute("select to_char(now() at time zone 'UTC', 'YYYYMMDD')")
                utc_day = cur.fetchone()[0]
                assert_(refs == [f"INC-{utc_day}-01", f"INC-{utc_day}-02"],
                        f"refs must carry the UTC day and advance within it: {refs}")

            @case2("[tier 2] the OLD formula really does collide on this server")
            def _(assert_, cur=cur, schema=schema):
                redirected = SchemaRedirect(cur, schema)
                cur.execute("select to_char(now() at time zone 'UTC', 'YYYYMMDD')")
                client_utc_day = cur.fetchone()[0]
                cur.execute(f"delete from {schema}.incident")
                first = old_next_incident_ref(redirected, client_utc_day)
                cur.execute(f"insert into {schema}.incident (ref) values (%s)", (first,))
                second = old_next_incident_ref(redirected, client_utc_day)
                cur.execute("select to_char(now(), 'YYYYMMDD')")
                server_day = cur.fetchone()[0]
                if server_day == client_utc_day:
                    assert_(second != first,
                            "on a session whose day already matches UTC the old formula "
                            "cannot collide, and must number the second incident 02")
                    return
                assert_(first == second,
                        f"across the boundary the old formula must number both incidents "
                        f"the same: {first!r} vs {second!r}")
                cur.execute("savepoint expecting_collision")
                try:
                    cur.execute(f"insert into {schema}.incident (ref) values (%s)", (second,))
                except psycopg.errors.UniqueViolation:
                    cur.execute("rollback to savepoint expecting_collision")
                    return
                assert_(False, "the old formula must violate the ref unique constraint here")

            ALL_FAILURES.extend(run_cases(only_tier2=True))
        conn.rollback()   # the probe schema and every row in it go with it


def run_cases(only_tier2: bool = False) -> list[str]:
    failures = []
    for name, fn in list(CASES):
        if only_tier2 != name.startswith("[tier 2]"):
            continue
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")
    return failures


ALL_FAILURES: list[str] = []


def main():
    ALL_FAILURES.extend(run_cases())

    dsn = os.environ.get("CARR_INCIDENT_NUMBERING_DSN")
    if not dsn:
        print("\n(tier 2 not run: set CARR_INCIDENT_NUMBERING_DSN to a throwaway "
              "Postgres to insert two real incidents in a non-UTC session)")
    else:
        try:
            import psycopg  # noqa: F401
        except ImportError:
            print("[FAIL] [tier 2] psycopg is not installed")
            ALL_FAILURES.append("[tier 2] psycopg import")
        else:
            tier2(dsn)

    total = len(CASES)
    print(f"\n{total - len(ALL_FAILURES)}/{total} passed")
    return 1 if ALL_FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
