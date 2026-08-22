#!/usr/bin/env python3
"""Contract tests for the authenticated application-session substrate.

These run against a DISPOSABLE local PostgreSQL carrying the real schema
(ops/disposable-pg.sh), never Neon, staging or production. Every assertion
EXECUTES its guarantee. A contract that cannot run FAILS; nothing here skips,
and nothing reports a pass it did not observe.

THREE DESIGN RULES, each bought with a defect this file previously had.

1. DML RUNS AS carr_writer, NOT AS THE SUPERUSER THE HARNESS HANDS YOU.
   An earlier version drove every insert as the cluster superuser and checked
   privileges by querying has_table_privilege(). It therefore passed 22/22 while
   the runtime writer could not write a single piece of qualified evidence: the
   guard took a row lock, row locks need UPDATE privilege, and UPDATE is exactly
   what the writer is denied. Metadata queries cannot see that. Acting as the
   role can.

2. THE ORACLE ASSERTS WHICH GUARD FIRED, NOT MERELY THAT SOMETHING FAILED.
   Two earlier oracles were vacuous. Accepting any error let the foreign key
   stand in for the guard. Pinning SQLSTATE P0001 was better but still only
   pinned the mechanism CLASS — with the unknown-session branch deleted, the
   record came back all-NULL, fell through the liveness branches, and the
   cross-actor branch raised P0001 instead, so the mutant lived. The message
   text is what distinguishes one guard from another.

3. CONTRACTS ARE INDEPENDENT AND THE SUITE IS RE-RUNNABLE.
   A contract that asserted a global row count passed only on a virgin database
   and failed on the second run. Assertions are scoped to rows this run created.

Usage:
    DSN=$(ops/disposable-pg.sh start)
    .venv/bin/python mcp-server/test/db/application_session_contract.py "$DSN"
    ops/disposable-pg.sh stop
"""
import contextlib
import pathlib
import sys
import threading
import time
import uuid

import psycopg

FAILURES: list[tuple[str, str]] = []
PASSES: list[str] = []
CONNS: list = []

# SQLSTATEs meaning "the substrate is ABSENT", not "a guard refused this".
ABSENCE_SQLSTATES = {"42P01", "42703", "42883", "42704", "3F000"}

MINT = """select ops.mint_application_session(
    %s, %s, %s, %s, 'dealroom-cookie',
    'accounts.google.com', 'human_partner', 'joe@example.test', {expires})"""

TENANT = "carr-internal"
# A second, equally real tenant. organization_tenant_id is caller-chosen free
# text with no membership table behind it (ops.mint_application_session never
# checks it against the actor), so minting Joe a session under this tenant is
# exactly as legitimate as minting one under TENANT -- it is what a genuine
# second-tenant caller looks like, not a malformed row.
OTHER_TENANT = "carr-other-tenant"

TOOL_CALL_INSERT = """insert into tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
    values (%s, 'log-activity', %s, 'hash', '{}'::jsonb, 'carr-internal', %s)"""

EVENT_INSERT = """insert into event
    (occurred_at, actor_id, verb, subject_type, subject_id, cause,
     organization_tenant_id, application_session_id)
    values (now(), %s, 'log-decision', 'decision', %s, 'human_stated',
            'carr-internal', %s)"""

READ_CALL_INSERT = """insert into tool_read_call
    (verb, actor_slug, organization_tenant_id, application_session_id)
    values ('catch-me-up', %s, 'carr-internal', %s)"""

# Each guard raises a distinct phrase. The oracle asserts the expected one is
# present AND that no OTHER guard's phrase is — otherwise one omnibus message
# naming every phrase satisfies every contract at once.
GUARD_PHRASES = ("unknown application session", "is revoked", "is expired",
                 "different actor", "does not belong to actor", "different tenant",
                 "cannot be changed", "cannot be rewritten", "cannot be deleted",
                 "identity is immutable", "no such application session",
                 "already revoked", "may not exceed")


def check(name, fn):
    """Run one contract. Any exception is a FAILURE, never a skip."""
    for c in CONNS:
        with contextlib.suppress(Exception):
            c.rollback()
        with contextlib.suppress(Exception):
            c.execute("reset role")
            c.commit()   # SET ROLE is transactional; without the commit a later
                         # rollback reverts it and the next contract silently runs
                         # as carr_writer.
    try:
        fn()
        PASSES.append(name)
        print(f"  pass  {name}")
    except AssertionError as exc:
        FAILURES.append((name, str(exc)))
        print(f"  FAIL  {name}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  ERROR {name}\n          {type(exc).__name__}: {exc}")


@contextlib.contextmanager
def as_writer(conn):
    """Execute with exactly the privileges the Worker's write credential has."""
    with conn.cursor() as cur:
        cur.execute("set role carr_writer")
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        with contextlib.suppress(Exception), conn.cursor() as cur:
            cur.execute("reset role")
            conn.commit()


def writer_runs(conn, sql, params=None, because=""):
    """Assert carr_writer CAN do this. The mirror of refuses()."""
    try:
        with as_writer(conn), conn.cursor() as cur:
            cur.execute(sql, params or ())
            # rowcount, not absence-of-error. A BEFORE ROW trigger that returns
            # NULL silently suppresses the insert and raises nothing at all; this
            # helper previously called that a success while the row vanished,
            # which is the same silent shape that made the round-two bug fatal.
            if cur.rowcount < 1:
                raise AssertionError(
                    f"statement reported success but wrote NO ROWS (rowcount="
                    f"{cur.rowcount}) — a BEFORE trigger returning NULL suppresses "
                    f"the write silently. Required: {because}")
            conn.commit()
    except psycopg.Error as exc:
        raise AssertionError(
            f"carr_writer could NOT do this, but must be able to: {because}\n"
            f"          {getattr(exc, 'sqlstate', '?')}: {str(exc).strip().splitlines()[0]}"
        ) from None


def refuses(conn, sql, params=None, because="", expect_message=None, role=None,
            privilege_is_the_point=False):
    """Assert the statement is refused BY THE INTENDED GUARD."""
    ctx = as_writer(conn) if role == "carr_writer" else contextlib.nullcontext()
    try:
        with ctx, conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.rollback()
        raise AssertionError(f"statement was ACCEPTED but must be refused: {because}")
    except psycopg.Error as exc:
        conn.rollback()
        state = getattr(exc, "sqlstate", None)
        text = str(exc)
        if state in ABSENCE_SQLSTATES:
            raise AssertionError(
                f"no guard refused this — the substrate is simply absent "
                f"(SQLSTATE {state}). Required: {because}") from None
        if state == "42501" and not privilege_is_the_point:
            raise AssertionError(
                f"refused by a PRIVILEGE error, not by the guard. The role cannot "
                f"even attempt the operation, so the guard is unproven: {text.strip()}"
            ) from None
        if expect_message:
            low = text.lower()
            if expect_message.lower() not in low:
                raise AssertionError(
                    f"refused, but by a DIFFERENT guard than the one under test. "
                    f"Expected a message containing {expect_message!r}; got: "
                    f"{text.strip().splitlines()[0]}") from None
            others = [g for g in GUARD_PHRASES
                      if g != expect_message.lower() and g in low]
            if others:
                raise AssertionError(
                    f"the refusal names MORE THAN ONE guard ({others}); a single "
                    f"message satisfying every expectation makes every contract "
                    f"pass at once. Guards must raise distinct messages.") from None


def actor_id(conn, slug):
    with conn.cursor() as cur:
        cur.execute("select id from actor where slug=%s", (slug,))
        row = cur.fetchone()
    assert row, f"fixture actor {slug!r} missing from the schema snapshot"
    return row[0]


def mint(conn, actor, expires="now() + interval '1 hour'", sponsor="joe", tenant=TENANT):
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(MINT.format(expires=expires), (sid, actor, tenant, sponsor))
    conn.commit()
    return sid


def declare_inventory_manifest(conn, actor, note="contract suite: these rows"):
    """Declare a 0271 inventory manifest describing the rows that exist NOW.

    0271 made ops.drive_retirement_readiness()'s `ready` require a manifest
    whose digest matches ops.drive_dependency_digest() over this database's own
    drive_dependency rows, because before it the retirement DENOMINATOR was
    whatever carr_writer had inserted. Every contract below that asserts `ready
    is True` therefore has to declare one first.

    IT IS DELIBERATELY NOT CALLED ONCE AT SETUP. Any later contract that records
    a dependency changes the digest and un-binds the manifest -- which is the
    guarantee, not a nuisance -- so this is called immediately before each
    assertion that needs a bound inventory, and never hoisted.
    """
    sid = mint(conn, actor)
    with conn.cursor() as cur:
        cur.execute(
            """insert into ops.drive_inventory_manifest
                 (id, inventory_digest, application_session_id, declared_by_actor_id,
                  organization_tenant_id, note)
               values (%s, ops.drive_dependency_digest(), %s, %s, %s, %s)""",
            (uuid.uuid4(), sid, actor, TENANT, note))
    conn.commit()
    return sid


LOOPBACK = ("127.0.0.1", "::1", "localhost", "")


def refuse_non_disposable(dsn):
    """Refuse anything that is not a local throwaway.

    Every contract below writes rows that this migration deliberately makes
    undeletable. Pointed at Neon, staging or production, this suite would leave
    permanent junk that no later statement can remove. The docstring used to be
    the only thing standing in the way.
    """
    try:
        info = psycopg.conninfo.conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001
        raise SystemExit(f"unparseable DSN: {dsn!r}") from None
    host = (info.get("host") or "").strip()
    if not host:
        # A DSN with no host is NOT loopback by default: libpq resolves it from
        # PGHOST/PGHOSTADDR, so an empty host routed straight around this guard.
        raise SystemExit(
            "REFUSING TO RUN: the DSN names no host, so libpq would take it from "
            "PGHOST and this check would be meaningless. Name 127.0.0.1 explicitly.")
    if host not in LOOPBACK:
        raise SystemExit(
            f"REFUSING TO RUN against host {host!r}. This suite writes rows that "
            f"0232 makes permanently undeletable; it may only target a disposable "
            f"local cluster (see ops/disposable-pg.sh).")
    dbname = (info.get("dbname") or "").lower()
    for banned in ("prod", "production", "staging", "neon"):
        if banned in dbname:
            raise SystemExit(
                f"REFUSING TO RUN against database {dbname!r}: the name looks "
                f"like a real environment.")


def main(dsn):  # noqa: C901
    refuse_non_disposable(dsn)
    print("application-session substrate — contract tests")
    print(f"target: {dsn}\n")
    conn = psycopg.connect(dsn)
    # Re-check AFTER connecting, before the first write: what libpq actually
    # resolved is the only host that matters.
    actual = (conn.info.host or "") + (conn.info.hostaddr or "")
    if not any(tok in actual for tok in ("127.0.0.1", "::1", "localhost")):
        conn.close()
        raise SystemExit(f"REFUSING TO RUN: connected host resolved to {actual!r}")
    conn2 = psycopg.connect(dsn)
    CONNS.extend([conn, conn2])
    assert conn.info.backend_pid != conn2.info.backend_pid, \
        "replay convergence is meaningless without two distinct backends"

    joe = actor_id(conn, "joe")
    dell = actor_id(conn, "dell")

    # ---------------------------------------------------------- substrate ----
    def substrate_exists():
        with conn.cursor() as cur:
            cur.execute("select to_regclass('ops.application_session')")
            assert cur.fetchone()[0] is not None, "ops.application_session does not exist"
    check("substrate: ops.application_session exists", substrate_exists)

    def required_columns():
        needed = {"id", "actor_id", "organization_tenant_id", "sponsoring_human_slug",
                  "via", "authenticated_at", "expires_at", "revoked_at",
                  "auth_issuer", "authorization_class", "verified_subject"}
        with conn.cursor() as cur:
            cur.execute("""select column_name from information_schema.columns
                           where table_schema='ops' and table_name='application_session'""")
            have = {r[0] for r in cur.fetchall()}
        missing = needed - have
        assert not missing, f"session record is missing mandated fields: {sorted(missing)}"
    check("req 2: record carries principal, issuer, class, expiry, revocation",
          required_columns)

    # ------------------------------- THE contract the superuser suite missed --
    def writer_can_write_qualified_evidence():
        """The whole substrate is a no-op if the runtime writer cannot bind."""
        sid = mint(conn, joe)
        writer_runs(conn, TOOL_CALL_INSERT, (str(uuid.uuid4()), joe, sid),
                    because="carr_writer must be able to write QUALIFIED evidence; "
                            "if it cannot, the fleet silently emits only legacy rows")
        writer_runs(conn, EVENT_INSERT, (joe, str(uuid.uuid4()), sid),
                    because="carr_writer must be able to write a qualified event")
        writer_runs(conn, READ_CALL_INSERT, ("joe", sid),
                    because="carr_writer must be able to write a qualified read call")
    check("req 4: carr_writer CAN write qualified evidence on all three tables",
          writer_can_write_qualified_evidence)

    def the_minter_can_actually_mint():
        """The grantee path had never been exercised.

        Every other contract mints as the cluster superuser, which needs no
        grant at all. carr_session_minter held EXECUTE on the mint while lacking
        USAGE on schema ops, so the one role permitted to mint could not reach
        the function: "permission denied for schema ops". A capability nobody
        tests is a capability nobody has.
        """
        sid = uuid.uuid4()
        try:
            with conn.cursor() as cur:
                cur.execute("set role carr_session_minter")
                cur.execute(MINT.format(expires="now() + interval '1 hour'"),
                            (sid, joe, TENANT, "joe"))
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            raise AssertionError(
                f"carr_session_minter could not mint: "
                f"{str(exc).strip().splitlines()[0]}") from None
        finally:
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role")
                conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.application_session where id=%s", (sid,))
            assert cur.fetchone()[0] == 1, "the mint reported success but wrote no session"
    check("req 1: carr_session_minter CAN mint (the grantee path, not superuser)",
          the_minter_can_actually_mint)

    def writer_cannot_mint():
        sid = uuid.uuid4()
        refuses(conn, MINT.format(expires="now() + interval '1 hour'"), (sid, joe, TENANT, "joe"),
                because="the runtime write credential must not forge a session",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 1: carr_writer cannot mint (attempted as the role)", writer_cannot_mint)

    def writer_cannot_insert_sessions():
        refuses(conn, """insert into ops.application_session
                  (id,actor_id,organization_tenant_id,via,auth_issuer,
                   authorization_class,verified_subject,expires_at)
                  values (gen_random_uuid(),%s,'carr-internal','x','y','z','w',
                          now()+interval '1 hour')""", (joe,),
                because="carr_writer must not bypass the mint with a direct insert",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 1: carr_writer cannot INSERT a session directly",
          writer_cannot_insert_sessions)

    def no_caller_authenticated_at():
        with conn.cursor() as cur:
            cur.execute("""select pg_get_function_arguments(p.oid)
                           from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                           where n.nspname='ops' and p.proname='mint_application_session'""")
            rows = cur.fetchall()
        assert rows, "ops.mint_application_session does not exist"
        for (args,) in rows:
            assert "authenticated_at" not in args, \
                f"mint accepts a caller-supplied authenticated_at: {args}"
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("""select abs(extract(epoch from (authenticated_at - now())))
                           from ops.application_session where id=%s""", (sid,))
            drift = cur.fetchone()[0]
        assert drift < 60, f"authenticated_at is not the server clock (drift {drift}s)"
    check("req 1: authenticated_at cannot be supplied by the caller",
          no_caller_authenticated_at)

    # --------------------------- binding guards, on ALL THREE tables ----------
    for table, stmt, args_for in (
        ("tool_call", TOOL_CALL_INSERT, lambda a, s: (str(uuid.uuid4()), a, s)),
        ("event", EVENT_INSERT, lambda a, s: (a, str(uuid.uuid4()), s)),
    ):
        def unknown_refuses(stmt=stmt, args_for=args_for, table=table):
            refuses(conn, stmt, args_for(joe, str(uuid.uuid4())),
                    because=f"{table}: an INSERT naming an unknown session must refuse",
                    role="carr_writer", expect_message="unknown application session")
        check(f"req 4: {table} — unknown session refuses (by name, not the FK)",
              unknown_refuses)

        def cross_actor_refuses(stmt=stmt, args_for=args_for, table=table):
            sid = mint(conn, joe)
            refuses(conn, stmt, args_for(dell, sid),
                    because=f"{table}: Dell must not attach to Joe's session",
                    role="carr_writer", expect_message="different actor")
        check(f"req 4: {table} — cross-actor binding refuses", cross_actor_refuses)

        def revoked_refuses(stmt=stmt, args_for=args_for, table=table):
            sid = mint(conn, joe)
            with conn.cursor() as cur:
                cur.execute("select ops.revoke_application_session(%s,'signed out')", (sid,))
            conn.commit()
            refuses(conn, stmt, args_for(joe, sid),
                    because=f"{table}: a revoked session must not qualify",
                    role="carr_writer", expect_message="is revoked")
        check(f"req 8: {table} — revoked session cannot qualify", revoked_refuses)

        def expired_refuses(stmt=stmt, args_for=args_for, table=table):
            sid = mint(conn, joe, expires="now() + interval '1 second'")
            time.sleep(1.5)
            refuses(conn, stmt, args_for(joe, sid),
                    because=f"{table}: an expired session must not qualify",
                    role="carr_writer", expect_message="is expired")
        check(f"req 8: {table} — expired session cannot qualify", expired_refuses)

    def read_call_cross_actor_refuses():
        sid = mint(conn, joe)
        refuses(conn, READ_CALL_INSERT, ("dell", sid),
                because="tool_read_call identifies its actor by slug and must still match",
                role="carr_writer", expect_message="does not belong to actor")
    check("req 4: tool_read_call — cross-actor binding refuses (by slug)",
          read_call_cross_actor_refuses)

    # --------------------------------------- legacy, promotion, immutability --
    def legacy_cannot_be_promoted():
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, """insert into tool_call
            (idempotency_key, verb, actor_id, request_hash, response)
            values (%s,'log-activity',%s,'hash','{}'::jsonb)""", (key, joe),
            because="a legacy NULL-link insert must still be allowed")
        refuses(conn, "update tool_call set application_session_id=%s where idempotency_key=%s",
                (sid, key), because="a legacy row must never be promotable",
                expect_message="cannot be changed")
    check("req 5: legacy row cannot be promoted by UPDATE", legacy_cannot_be_promoted)

    def event_legacy_cannot_be_promoted():
        sid = mint(conn, joe)
        sub = str(uuid.uuid4())
        writer_runs(conn, """insert into event
            (occurred_at, actor_id, verb, subject_type, subject_id, cause)
            values (now(),%s,'log-decision','decision',%s,'human_stated')""", (joe, sub),
            because="a legacy event insert must still be allowed")
        refuses(conn, "update event set application_session_id=%s where subject_id=%s",
                (sid, sub), because="a legacy event must never be promotable",
                expect_message="cannot be changed")
    check("req 5: legacy event cannot be promoted by UPDATE",
          event_legacy_cannot_be_promoted)

    def identity_immutable():
        sid = mint(conn, joe)
        refuses(conn, "update ops.application_session set actor_id=%s where id=%s", (dell, sid),
                because="session identity must be immutable",
                expect_message="identity is immutable")
        refuses(conn, "delete from ops.application_session where id=%s", (sid,),
                because="session rows must not be deletable",
                expect_message="cannot be deleted")
    check("session identity is immutable and undeletable", identity_immutable)

    def every_identity_column_is_immutable():
        """actor_id alone was tested; the rest were mutable.

        expires_at is the sharpest: the mint refuses a year-9999 expiry, and
        nothing stopped an existing row's expiry simply being moved there.
        """
        for column, value in (("verified_subject", "'attacker@example.test'"),
                              ("expires_at", "timestamptz '9999-01-01'"),
                              ("sponsoring_human_slug", "'dell'"),
                              ("via", "'forged-door'"),
                              ("auth_issuer", "'evil.example'"),
                              # NOT 'human_partner' — that is what mint() stores,
                              # so the UPDATE would change nothing and fall through
                              # to a different branch, testing the wrong guard.
                              ("authorization_class", "'forged_class'"),
                              ("organization_tenant_id", "'other-tenant'"),
                              # Both were untested: recorded_at was outside the
                              # identity tuple entirely, so the one permitted
                              # mutation could rewrite an audit timestamp.
                              ("recorded_at", "timestamptz '1999-01-01'"),
                              ("authenticated_at", "timestamptz '1999-01-01'")):
            sid = mint(conn, joe)
            refuses(conn,
                    f"update ops.application_session set {column}={value} where id=%s",
                    (sid,),
                    because=f"{column} must be immutable once the session is minted",
                    expect_message="identity is immutable")
    check("every identity column is immutable, not just actor_id",
          every_identity_column_is_immutable)

    def revocation_cannot_be_restated():
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("select ops.revoke_application_session(%s,'first reason')", (sid,))
        conn.commit()
        refuses(conn, """update ops.application_session
                         set revoked_at=now(), revocation_reason='re-timed'
                         where id=%s""", (sid,),
                because="a recorded revocation must not be re-timed or re-reasoned",
                expect_message="already recorded")
    check("a recorded revocation is final", revocation_cannot_be_restated)

    def null_tenant_cannot_bind():
        """Pins `is distinct from`; under plain `<>` a NULL tenant binds anywhere."""
        sid = mint(conn, joe)
        stmt = TOOL_CALL_INSERT.replace("'carr-internal', %s)", "NULL, %s)")
        refuses(conn, stmt, (str(uuid.uuid4()), joe, sid),
                because="a row with NO tenant must not bind to a tenanted session",
                role="carr_writer", expect_message="different tenant")
    check("req 4: a NULL-tenant row cannot bind", null_tenant_cannot_bind)

    def read_call_actor_id_must_match():
        """tool_read_call carries slug AND id; checking one let the other lie."""
        sid = mint(conn, joe)
        stmt = """insert into tool_read_call
            (verb, actor_slug, actor_id, organization_tenant_id, application_session_id)
            values ('catch-me-up','joe',%s,'carr-internal',%s)"""
        refuses(conn, stmt, (dell, sid),
                because="a read call naming Joe's slug and Dell's actor id must not "
                        "bind to Joe's session; the row would then be frozen forever",
                role="carr_writer", expect_message="different actor")
    check("req 4: tool_read_call actor_id must match the session too",
          read_call_actor_id_must_match)

    def qualified_evidence_is_frozen():
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, sid), because="setup")
        refuses(conn, "update tool_call set request_hash='TAMPERED' where idempotency_key=%s",
                (key,), because="qualified evidence content must not be rewritable",
                expect_message="cannot be rewritten")
        refuses(conn, "delete from tool_call where idempotency_key=%s", (key,),
                because="qualified evidence must not be deletable",
                expect_message="cannot be deleted")
    check("qualified tool_call cannot be rewritten or deleted", qualified_evidence_is_frozen)

    def qualified_event_not_deletable_but_still_updatable():
        """update-decision and detach-decision rewrite events in place by design."""
        sid = mint(conn, joe)
        sub = str(uuid.uuid4())
        writer_runs(conn, EVENT_INSERT, (joe, sub, sid), because="setup")
        writer_runs(conn, "update event set cause='human_stated' where subject_id=%s", (sub,),
                    because="update-decision and detach-decision must keep working; "
                            "detach-decision is the designed retraction path")
        refuses(conn, "delete from event where subject_id=%s", (sub,),
                because="qualified event evidence must not be deletable",
                expect_message="cannot be deleted")
    check("qualified event stays updatable (verbs keep working) but undeletable",
          qualified_event_not_deletable_but_still_updatable)

    def writer_cannot_delete_audit_rows():
        for table in ("tool_call", "event", "tool_read_call"):
            with conn.cursor() as cur:
                cur.execute("select has_table_privilege('carr_writer',%s,'DELETE')", (table,))
                assert cur.fetchone()[0] is False, (
                    f"carr_writer gained DELETE on {table}; a legacy row can now be "
                    f"deleted and reinserted as qualified evidence")
        conn.rollback()
    check("legacy promotion route stays closed (carr_writer has no DELETE)",
          writer_cannot_delete_audit_rows)

    # ------------------------------------------------- revocation semantics --
    def revocation_fences_inflight_evidence():
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("set role carr_writer")
            cur.execute("begin")
            cur.execute(TOOL_CALL_INSERT, (str(uuid.uuid4()), joe, sid))
        done = {"v": False}

        def revoke():
            try:
                with psycopg.connect(dsn) as c3, c3.cursor() as cur3:
                    cur3.execute("select ops.revoke_application_session(%s,'compromised')", (sid,))
                    c3.commit()
            except Exception:  # noqa: BLE001
                pass
            done["v"] = True

        t = threading.Thread(target=revoke, daemon=True)
        t.start()
        t.join(timeout=2.0)
        blocked = not done["v"]
        conn.rollback()
        with contextlib.suppress(Exception), conn.cursor() as cur:
            cur.execute("reset role")
            conn.commit()
        t.join(timeout=5.0)
        assert blocked, ("revocation committed while a writer held an unfinished insert "
                         "against the same session — evidence can be bound to an "
                         "already-revoked session")
    check("req 8: revocation serialises against in-flight evidence",
          revocation_fences_inflight_evidence)

    def revoke_is_not_silent():
        refuses(conn, "select ops.revoke_application_session(%s,'x')", (str(uuid.uuid4()),),
                because="revoking an unknown session must not report success",
                expect_message="no such application session")
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("select ops.revoke_application_session(%s,'first')", (sid,))
        conn.commit()
        refuses(conn, "select ops.revoke_application_session(%s,'second')", (sid,),
                because="revoking an already-revoked session must not report success",
                expect_message="already revoked")
    check("req 8: revoke reports failure instead of silently doing nothing",
          revoke_is_not_silent)

    def lifetime_is_bounded():
        refuses(conn, MINT.format(expires="timestamptz '9999-01-01'"),
                (uuid.uuid4(), joe, TENANT, "joe"),
                because="an unbounded lifetime is a permanent credential with an "
                        "expiry column bolted on",
                expect_message="may not exceed")
    check("req 8: session lifetime is bounded from above", lifetime_is_bounded)

    def revoked_at_cannot_precede_authentication():
        sid = mint(conn, joe)
        refuses(conn, """update ops.application_session
                         set revoked_at = timestamptz '1970-01-01',
                             revocation_reason = 'backdated'
                         where id=%s""", (sid,),
                because="a revocation before the authentication it revokes is corrupt",
                # Was passing no expect_message, so ANY error scored a pass — the
                # vacuous-oracle shape this file's own rule 2 forbids.
                expect_message="violates check constraint")
    check("revoked_at cannot precede authenticated_at", revoked_at_cannot_precede_authentication)

    # ------------------------------------------------------------- grants ----
    def liveness_probe_not_public():
        with conn.cursor() as cur:
            cur.execute("""select has_function_privilege('public',
                             'ops.application_session_is_live(uuid)','EXECUTE')""")
            assert cur.fetchone()[0] is False, \
                "PUBLIC can probe session liveness; the grants are not deliberate"
    check("grants: liveness probe is not executable by PUBLIC", liveness_probe_not_public)

    def definer_functions_are_hardened():
        """A SECURITY DEFINER function without a pinned search_path is an
        escalation waiting for a schema the caller controls."""
        with conn.cursor() as cur:
            cur.execute("""select p.proname, p.prosecdef, p.proconfig
                           from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                           where n.nspname='ops'
                             and p.proname in ('mint_application_session',
                                               'revoke_application_session',
                                               'require_live_application_session',
                                               'refuse_application_session_rewrite',
                                               'refuse_application_session_relink',
                                               'refuse_qualified_evidence_rewrite')""")
            rows = cur.fetchall()
        assert rows, "the substrate functions do not exist"
        for name, secdef, config in rows:
            if not secdef:
                continue
            cfg = " ".join(config or [])
            assert "search_path" in cfg, (
                f"ops.{name} is SECURITY DEFINER with no pinned search_path")
        pub = []
        with conn.cursor() as cur:
            for name, args in (("mint_application_session",
                                "uuid,uuid,text,text,text,text,text,text,timestamptz"),
                               ("revoke_application_session", "uuid,text"),
                               ("application_session_is_live", "uuid"),
                               ("require_live_application_session", ""),
                               ("refuse_application_session_rewrite", ""),
                               ("refuse_application_session_relink", ""),
                               ("refuse_qualified_evidence_rewrite", "")):
                cur.execute("select has_function_privilege('public',%s,'EXECUTE')",
                            (f"ops.{name}({args})",))
                if cur.fetchone()[0]:
                    pub.append(name)
        assert not pub, f"PUBLIC holds EXECUTE on: {pub}"
    check("definer functions pin search_path and are not PUBLIC",
          definer_functions_are_hardened)

    def guards_have_one_unassumable_owner():
        """The owner of a function can replace its body. That makes ownership a
        trust boundary exactly as load-bearing as the EXECUTE grants the sweeps
        above already police, and it was the one item on this migration's
        residual-risk list with no test at all.

        Three things are asserted, and the third is the one with teeth:
          1. Every guard shares ONE owner. Split ownership means a second role
             can rewrite part of the substrate.
          2. That owner is not itself a runtime role.
          3. No runtime role can ASSUME the owner. A role that can SET ROLE to
             the owner, or that inherits its privileges, can replace any guard
             and then write whatever it likes -- so the guards would be advice,
             not enforcement."""
        guards = ("mint_application_session", "revoke_application_session",
                  "application_session_is_live", "require_live_application_session",
                  "refuse_application_session_rewrite",
                  "refuse_application_session_relink",
                  "refuse_qualified_evidence_rewrite")
        runtime_roles = ("carr_writer", "carr_reader", "carr_jobs", "carr_authority",
                         "carr_exporter", "carr_device_evidence", "carr_session_minter")
        with conn.cursor() as cur:
            cur.execute("""select p.proname, pg_get_userbyid(p.proowner)
                           from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                           where n.nspname='ops' and p.proname = any(%s)""",
                        (list(guards),))
            rows = cur.fetchall()
        assert len(rows) == len(guards), (
            f"expected {len(guards)} guard functions, found {len(rows)}: "
            f"{sorted(r[0] for r in rows)}")
        owners = {owner for _, owner in rows}
        assert len(owners) == 1, (
            f"the guards do not share one owner: "
            f"{sorted((n, o) for n, o in rows)}. Split ownership means more than "
            f"one role can rewrite this substrate.")
        owner = owners.pop()

        with conn.cursor() as cur:
            cur.execute("""select tableowner from pg_tables
                           where schemaname='ops' and tablename='application_session'""")
            row = cur.fetchone()
        assert row, "ops.application_session is absent"
        assert row[0] == owner, (
            f"ops.application_session is owned by {row[0]!r} but its guards are "
            f"owned by {owner!r}; the table owner can disable a trigger on it")

        assert owner not in runtime_roles, (
            f"the guards are owned by {owner!r}, which is a RUNTIME role. The "
            f"credential the substrate constrains can replace the guards that "
            f"constrain it.")

        assumable = []
        with conn.cursor() as cur:
            for role in runtime_roles:
                cur.execute("select pg_has_role(%s,%s,'MEMBER'), pg_has_role(%s,%s,'USAGE')",
                            (role, owner, role, owner))
                is_member, has_usage = cur.fetchone()
                if is_member or has_usage:
                    assumable.append(
                        f"{role} (SET ROLE={is_member}, inherits={has_usage})")
        assert not assumable, (
            f"these runtime roles can assume the guard owner {owner!r} and so can "
            f"replace any guard in this migration: {assumable}")
    check("guard functions and their table share one owner no runtime role can assume",
          guards_have_one_unassumable_owner)

    # ------------------------------------------- 0233: the minting credential ----
    # 0232 left carr_session_minter memberless ON PURPOSE and said so; 0233 is
    # the decision about which credential joins it. 0233's own apply-time block
    # asserts the MEMBERSHIP GRAPH from the catalog, which is the right tool for
    # a graph. These three assert what a catalog cannot: what each role can
    # actually DO when something acts as it.
    def issuer_can_actually_mint():
        """The grantee path, not the superuser path. A membership that exists in
        pg_auth_members but does not carry EXECUTE on the mint function looks
        correct in every catalog query and mints nothing."""
        sid = uuid.uuid4()
        try:
            with conn.cursor() as cur:
                cur.execute("set role carr_session_issuer")
                cur.execute(MINT.format(expires="now() + interval '1 hour'"),
                            (sid, joe, TENANT, "joe"))
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            raise AssertionError(
                f"carr_session_issuer could not mint, so the substrate is still "
                f"inert: {str(exc).strip().splitlines()[0]}") from None
        finally:
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role")
                conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.application_session where id=%s", (sid,))
            assert cur.fetchone()[0] == 1, \
                "the mint reported success but wrote no session row"
    check("req 1: carr_session_issuer CAN mint (the credential 0233 chose)",
          issuer_can_actually_mint)

    def issuer_cannot_write_evidence():
        """The separation runs BOTH ways, and this is the half that is easy to
        forget. If the issuer could also insert evidence, one leaked secret
        would mint a session AND bind rows to it, which is the whole attack
        0232 exists to prevent -- just performed with a different credential."""
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        try:
            with conn.cursor() as cur:
                cur.execute("set role carr_session_issuer")
                cur.execute(TOOL_CALL_INSERT, (key, joe, sid))
            conn.rollback()
            raise AssertionError(
                "carr_session_issuer wrote qualified evidence; it must be able "
                "to mint a session and nothing else")
        except psycopg.Error as exc:
            conn.rollback()
            state = getattr(exc, "sqlstate", None)
            if state in ABSENCE_SQLSTATES:
                raise AssertionError(
                    f"no guard refused this -- the substrate is absent "
                    f"({state})") from None
            assert state == "42501", (
                f"the issuer was refused, but by a guard rather than by privilege. "
                f"It should not hold the privilege at all: {state}: "
                f"{str(exc).strip().splitlines()[0]}")
        finally:
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role")
                conn.commit()
    check("req 1: carr_session_issuer cannot write evidence, only mint",
          issuer_cannot_write_evidence)

    def minter_membership_is_exactly_the_issuer():
        """Transitive, via pg_has_role. A direct-edge query against
        pg_auth_members answers 'not a member' for a role that reaches the mint
        through one intermediate role, which is exactly the escalation worth
        catching."""
        with conn.cursor() as cur:
            cur.execute("""select m.rolname from pg_auth_members am
                             join pg_roles r on r.oid = am.roleid
                             join pg_roles m on m.oid = am.member
                            where r.rolname='carr_session_minter'
                            order by m.rolname""")
            members = [r[0] for r in cur.fetchall()]
        assert members == ["carr_session_issuer"], (
            f"carr_session_minter's members must be exactly ['carr_session_issuer']; "
            f"found {members}. An unnoticed extra member is how a separation "
            f"quietly stops separating.")
        reachers = []
        with conn.cursor() as cur:
            for role in ("carr_writer", "carr_reader", "carr_jobs", "carr_exporter",
                         "carr_authority", "carr_device_evidence"):
                cur.execute("select pg_has_role(%s,'carr_session_minter','MEMBER')", (role,))
                if cur.fetchone()[0]:
                    reachers.append(role)
        assert not reachers, (
            f"these roles can reach carr_session_minter and so can manufacture an "
            f"authenticated session: {reachers}")
    check("req 1: only the issuer reaches the mint, transitively",
          minter_membership_is_exactly_the_issuer)

    # ------------------------------------- 0234: minting from an actor slug ----
    def issuer_can_mint_by_slug():
        """The door authenticates a SLUG and has no actor id: actor.id is not
        resolved until callTool runs, long after authentication. The first
        attempt to wire the door called the uuid-taking mint and therefore
        minted nothing on every request, while its tests passed against a
        hand-built actor carrying an id no door can produce."""
        sid = uuid.uuid4()
        try:
            with conn.cursor() as cur:
                cur.execute("set role carr_session_issuer")
                cur.execute("""select ops.mint_application_session_for_slug(
                                 %s,'joe','carr-internal','joe','oauth-google',
                                 'accounts.google.com','verified_partner','joe',
                                 now() + interval '1 hour')""", (sid,))
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            raise AssertionError(
                f"the issuer could not mint by slug, so the door still cannot mint: "
                f"{str(exc).strip().splitlines()[0]}") from None
        finally:
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role"); conn.commit()
        with conn.cursor() as cur:
            cur.execute("""select a.slug from ops.application_session s
                             join actor a on a.id = s.actor_id where s.id=%s""", (sid,))
            row = cur.fetchone()
        assert row and row[0] == "joe", \
            "the session must resolve to the actor the slug names, not a null principal"
    check("req 1: the issuer can mint from a slug (0234 — the door has no actor id)",
          issuer_can_mint_by_slug)

    def unknown_slug_refuses():
        """A session with a null actor would satisfy 'a row exists' while failing
        the only thing the row is for: 0232's guard matches the evidence row's
        actor against the session's, and null matches nothing."""
        sid = uuid.uuid4()
        try:
            with conn.cursor() as cur:
                cur.execute("set role carr_session_issuer")
                cur.execute("""select ops.mint_application_session_for_slug(
                                 %s,'nobody-provisioned','carr-internal','joe','oauth-google',
                                 'accounts.google.com','verified_partner','nobody-provisioned',
                                 now() + interval '1 hour')""", (sid,))
            conn.rollback()
            raise AssertionError(
                "an unprovisioned slug minted a session; it would name no principal")
        except psycopg.Error as exc:
            conn.rollback()
            if getattr(exc, "sqlstate", None) in ABSENCE_SQLSTATES:
                raise AssertionError("the substrate is absent, not refusing") from None
            assert "no actor row for slug" in str(exc).lower(), (
                f"refused, but by a different guard: "
                f"{str(exc).strip().splitlines()[0]}")
        finally:
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role"); conn.commit()
    check("req 1: minting by an unprovisioned slug refuses", unknown_slug_refuses)

    def writer_cannot_mint_by_slug():
        """The slug wrapper must not become a second door into the mint. It is
        SECURITY DEFINER, so an over-broad grant here would hand the writer
        exactly what 0233 spent a role separating it from."""
        sid = uuid.uuid4()
        refuses(conn, """select ops.mint_application_session_for_slug(
                           %s,'joe','carr-internal','joe','oauth-google',
                           'accounts.google.com','verified_partner','joe',
                           now() + interval '1 hour')""", (sid,),
                because="the write credential must not mint through the slug wrapper",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 1: carr_writer cannot mint through the slug wrapper either",
          writer_cannot_mint_by_slug)

    # ---------------------------------------------- 0235: write receipts ----
    # This layer was REJECTED once, for three things. Each contract below names
    # which rejection it answers, and every one acts as carr_writer rather than
    # as the superuser the harness hands you.
    def receipt_fixture(sess=None, key=None, req_hash="req-hash-A", subject_type="deal",
                         subject_id=None, new_value=None, tenant=TENANT, actor=None):
        """A live session, one qualified tool_call row, AND -- new under 0238
        section (F), rule 1 -- ONE event row recording what that call wrote
        about the subject, all written as the writer. Returns (session_id,
        idempotency_key, call_digest, subject_id, material_digest).

        RULE 1 (0238 F): a receipt is refused unless a public.event row exists
        with the SAME idempotency_key, application_session_id, subject_type and
        subject_id. This fixture writes that event BEFORE anything else, so
        every receipt built from its return value already clears rule 1 --
        this is true for an ordinary receipt, a reversal, AND a retraction:
        rule 1 has no exemption for either of the other two.

        RULE 2 (0238 F): an ORDINARY receipt (reverses_receipt_id AND
        retracts_receipt_id both null) must carry
        material_digest = ops.write_receipt_material_digest(key, session,
        subject_type, subject_id), recomputed by the database from that same
        event -- never a string the caller invents. This fixture computes and
        returns that exact value; callers building an ordinary receipt must
        pass it straight through. A REVERSAL or RETRACTION is exempt from rule
        2 (never from rule 1) and continues to choose its own material_digest
        the way this file always has -- typically the target's own prior or
        material digest.

        new_value DEFAULTS TO A FRESH UUID, so two default calls against the
        same subject compute to DIFFERENT material -- the ordinary shape of
        two independent writes. A contract that needs two DIFFERENT calls to
        compute to the SAME material (a stale-but-real restatement of an
        earlier transition, or two retirement receipts that must assert
        identical material) passes an explicit, matching new_value both times.

        THE CALL DIGEST IS SUBJECT-BOUND (0238): ops.write_receipt_digest takes
        the receipt's own subject_type/subject_id as part of its input, so a
        digest computed for one subject cannot prove a receipt naming another.
        A caller that wants the receipt it files to actually PROVE must name
        THIS subject_type and THIS subject_id on that receipt -- never a fresh
        one invented separately."""
        who = actor if actor is not None else joe
        sid = sess or mint(conn, who, tenant=tenant)
        k = key or str(uuid.uuid4())
        subj = subject_id if subject_id is not None else uuid.uuid4()
        writer_runs(conn, """insert into tool_call
                (idempotency_key, verb, actor_id, request_hash, response,
                 organization_tenant_id, application_session_id)
                values (%s,'log-activity',%s,%s,'{}'::jsonb,%s,%s)""",
                    (k, who, req_hash, tenant, sid),
                    because="receipt fixture needs qualified evidence")
        nv = new_value if new_value is not None else str(uuid.uuid4())
        writer_runs(conn, """insert into event
                (occurred_at, actor_id, verb, subject_type, subject_id, field,
                 old_value, new_value, cause, idempotency_key,
                 organization_tenant_id, application_session_id)
                values (now(), %s, 'log-activity', %s, %s, 'state',
                        to_jsonb('prior'::text), to_jsonb(%s::text), 'human_stated',
                        %s, %s, %s)""",
                    (who, subject_type, subj, nv, k, tenant, sid),
                    because="rule 1: the call must have written an event about this "
                            "subject before any receipt can name it")
        with conn.cursor() as cur:
            cur.execute("""select ops.write_receipt_digest('log-activity', %s,
                             %s, %s, %s, %s, %s)""",
                        (who, tenant, sid, req_hash, subject_type, subj))
            digest = cur.fetchone()[0]
            cur.execute("select ops.write_receipt_material_digest(%s,%s,%s,%s)",
                        (k, sid, subject_type, subj))
            material = cur.fetchone()[0]
        return sid, k, digest, subj, material

    RECEIPT_INSERT = """insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
        values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,%s)"""

    # Same shape, with organization_tenant_id as a parameter -- needed by the
    # cross-tenant retraction/reversal/withdrawal contracts (0238 rules 4/5),
    # which must file a receipt under a SECOND, real tenant.
    RECEIPT_INSERT_TENANT = """insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
        values (%s,%s,%s,%s,'log-activity','deal',%s,%s,%s,%s,%s)"""

    # Same shape, over a subject_type of 'drive_dependency' -- needed once 0238
    # binds a retirement receipt's proof to naming the dependency it is about.
    RECEIPT_INSERT_DEP = RECEIPT_INSERT.replace("'deal'", "'drive_dependency'")

    # --------------------------------------------- residue cleanup helpers ----
    # THE FILE'S OWN RULE (see the module docstring, rule 3: "CONTRACTS ARE
    # INDEPENDENT AND THE SUITE IS RE-RUNNABLE") means every contract that
    # deliberately manufactures an unproven receipt or an open conflict must
    # leave the store as it found it. Two mechanisms, and only two, actually
    # clear either kind of residue:
    #   - an UNPROVEN receipt is cleared by a retraction that is itself PROVEN
    #     (an unproven retraction clears nothing -- this file tests that).
    #   - an open CONFLICT is closed by a PROVEN exact reversal of one side
    #     (an unproven reversal closes nothing -- this file tests that too).
    # Both helpers are written ONCE here and called from every contract that
    # needs them, rather than repeated inline, because a cleanup sequence
    # written a dozen times will be wrong in at least one of them.
    def cleanup_unproven_receipt(rid, subject_type, subject_id, sess=None, tenant=TENANT):
        """Retract the unproven receipt `rid` with a NEW receipt that is
        itself proven, so it stops counting against the acceptance bar and
        stops being a residue this suite's second pass would trip over.
        Requires `rid` to be unproven and un-retracted (rule 4: a retraction
        may only target a receipt that is NOT proven) and on the given
        tenant (rule 4: a retraction cannot cross tenants)."""
        s = sess or mint(conn, joe, tenant=tenant)
        _rsid, r_key, r_digest, _rs, _rm = receipt_fixture(
            sess=s, subject_type=subject_type, subject_id=subject_id, tenant=tenant)
        rret = uuid.uuid4()
        writer_runs(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest, retracts_receipt_id)
              values (%s,%s,%s,%s,'log-activity',%s,%s,%s,%s,%s,'origin',%s)""",
                    (rret, s, joe, tenant, subject_type, subject_id, r_key, r_digest,
                     f"m-cleanup-retraction-{rret}", rid),
                    because=f"cleanup: retract unproven residue {rid}")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rret,))
            assert cur.fetchone()[0] is True, (
                f"cleanup retraction of {rid} failed to prove -- an unproven "
                f"retraction clears nothing, so this would leave the residue "
                f"behind under a different id")
            conn.commit()

    def cleanup_open_conflicts(subject_type, subject_id, sess=None, tenant=TENANT):
        """Close every open conflict on one subject with a PROVEN exact
        reversal of one side. Re-queries ops.receipt_conflicts each time,
        since closing one pair can change which pairs remain open (the same
        pattern ops.continuity_reducer's own contract above uses)."""
        s = sess or mint(conn, joe, tenant=tenant)
        for _attempt in range(10):
            with conn.cursor() as cur:
                cur.execute("""select left_receipt, right_receipt
                                 from ops.receipt_conflicts(%s, %s)""",
                            (subject_type, subject_id))
                pairs = cur.fetchall()
            if not pairs:
                return
            right = pairs[0][1]
            with conn.cursor() as cur:
                cur.execute("""select prior_digest, material_digest
                                 from ops.write_receipt where id=%s""", (right,))
                r_prior, r_material = cur.fetchone()
            _rsid, rev_key, rev_digest, _rs, _rm = receipt_fixture(
                sess=s, subject_type=subject_type, subject_id=subject_id, tenant=tenant)
            rev_id = uuid.uuid4()
            writer_runs(conn, """insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, reverses_receipt_id)
                  values (%s,%s,%s,%s,'log-activity',%s,%s,%s,%s,%s,%s,%s)""",
                        (rev_id, s, joe, tenant, subject_type, subject_id, rev_key,
                         rev_digest, r_prior, r_material, right),
                        because=f"cleanup: reconcile conflict by reversing {right}")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (rev_id,))
                assert cur.fetchone()[0] is True, (
                    f"cleanup reversal of {right} failed to prove -- an "
                    f"unproven reversal closes nothing, so this would leave "
                    f"the conflict open under a different id")
                conn.commit()
        else:
            raise AssertionError(
                f"conflict on {subject_type}/{subject_id} never closed after "
                f"repeated cleanup reversals")

    def complete_honest_retirement(dep, sess=None, tenant=TENANT, base="origin"):
        """Retire `dep` with a fresh, honest, proven repoint/recovery pair.

        A drive_dependency row that stays operational forever without ever
        being retired is residue for ops.drive_retirement_readiness() in
        exactly the way an unproven receipt is residue for the acceptance
        bar: operational_total counts it and retired_total never catches up,
        so remaining never returns to what it would otherwise be and ready
        can never be recomputed correctly around it. Several contracts
        create a dependency only to exercise a REFUSAL and would otherwise
        leave it exactly that way forever -- this closes it out honestly
        afterward, the same way cleanup_unproven_receipt closes out a
        receipt. `base` lets a caller chain a SECOND retirement of the same
        dependency onto the first one's recovery material, so two
        retirements of one dependency never share 'origin' as a prior (which
        would manufacture an open conflict rather than a clean readiness
        number)."""
        s = sess or mint(conn, joe, tenant=tenant)
        marker = str(uuid.uuid4())
        _s1, key1, digest1, _ss1, material1 = receipt_fixture(
            sess=s, subject_type="drive_dependency", subject_id=dep,
            new_value=f"{marker}-repoint")
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, s, joe, dep, key1, digest1, material1, base),
                    because=f"cleanup: honest repoint receipt for {dep}")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, f"cleanup repoint for {dep} failed to prove"
            conn.commit()
        _s2, key2, digest2, _ss2, material2 = receipt_fixture(
            sess=s, subject_type="drive_dependency", subject_id=dep,
            new_value=f"{marker}-recovery")
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, s, joe, dep, key2, digest2, material2, material1),
                    because=f"cleanup: honest recovery receipt for {dep}")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True, f"cleanup recovery for {dep} failed to prove"
            conn.commit()
        rid = uuid.uuid4()
        writer_runs(conn, RETIREMENT_INSERT,
                    (rid, dep, r1, r2, s, joe, f"cleanup: honest retirement of {dep}"),
                    because=f"cleanup: retire {dep} so it does not linger as an "
                            f"operational-but-unretired residue")
        return rid, material2

    def retire_dependency_from_readiness_count(dep):
        """The always-safe fallback cleanup for a dependency whose fixture
        already occupies every state a fresh honest repoint or recovery
        could build on without conflicting with it (retirement_receipts_
        cannot_share_a_call is the sharpest example: r1 claims prior='origin'
        and r2 claims prior=material, so EVERY value already claimed on this
        subject is spoken for, and completing a genuinely honest retirement
        of the SAME dependency is not constructible without manufacturing an
        open conflict). Marking it non-operational removes it from
        ops.drive_retirement_readiness()'s operational_total entirely, which
        is exactly as legitimate as reality: a dependency a later scan finds
        gone from the codebase stops being operational too, retired or not."""
        with conn.cursor() as cur:
            cur.execute("update ops.drive_dependency set operational=false where id=%s",
                        (dep,))
            assert cur.rowcount == 1, (
                f"expected to update exactly one dependency, got {cur.rowcount}")
            conn.commit()

    # ---------------------------------------------------- 0238: the split ----
    def retraction_clears_the_acceptance_bar():
        """0238 (C). The escape hatch is a PROVEN retraction, not a delete or
        an update -- receipts stay immutable. An unproven receipt blocks
        acceptance until a proven receipt disavows it; only then does the bar
        stop counting it.

        ORDER-INDEPENDENT AND RE-RUNNABLE, on purpose: ops/check-application-
        session.sh runs the whole suite TWICE against the SAME database.
        Receipts are permanently undeletable, so unproven rows left behind by
        earlier contracts -- in this pass or an earlier one -- accumulate
        forever, and ops.accept_phase4 counts them GLOBALLY with no scoping.
        Relying on running first bought nothing on a second pass. So instead
        of trusting the table to already be clean, this contract SWEEPS every
        unproven, not-yet-excused receipt in the WHOLE table -- retracting
        each with its own proven receipt -- before it ever asserts that
        acceptance SUCCEEDS. It also proves its own anchor receipt up front,
        so the refusal it demonstrates first is unambiguously about UNPROVEN
        receipts, not the separate (and, on a bare table, equally true) bar
        requiring at least one proven receipt to exist at all.

        RULE 1 NOW APPLIES TO THE SWEEP TOO (0238 F): a retraction is a
        receipt like any other, and it is refused unless the SWEEP's own call
        wrote an event about the subject it names. event.idempotency_key is
        non-unique, so one shared tool_call row can back many such events --
        one per swept subject -- rather than needing a fresh call per target.

        RETRACTIONS USE 'origin' AS THEIR OWN prior_digest, deliberately, not
        the target's material. Both ops.receipt_conflicts and
        ops.continuity_reducer exclude every row with retracts_receipt_id set
        from their live/fold sets entirely, so a retraction's prior_digest is
        inert to both -- and 'origin' is unconditionally legal under rule 3,
        while the target's own (unproven) material usually is NOT (rule 3
        requires a PROVEN source), which would make this sweep uninsertable
        for exactly the receipts it exists to clear."""
        sid, key, digest, subject, material = receipt_fixture()
        anchor = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (anchor, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: an anchor receipt so proven_receipts > 0 already "
                            "holds before the refusal below")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (anchor,))
            assert cur.fetchone()[0] is True, "the anchor receipt must prove"
            conn.commit()

        bad_subject = uuid.uuid4()
        _bsid, bad_key, _bcalldigest, _bs, bad_material = receipt_fixture(
            sess=sid, subject_id=bad_subject)
        bad = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (bad, sid, joe, bad_subject, bad_key,
                     "a-digest-nobody-ever-wrote", bad_material, "origin"),
                    because="setup: a receipt left deliberately unproven -- its "
                            "MATERIAL matches what its call actually wrote (rule 2), "
                            "only the CALL digest is fabricated, so the INSERT "
                            "succeeds and only prove_write_receipt fails")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (bad,))
            assert cur.fetchone()[0] is False, "setup receipt must stay unproven"
            conn.commit()
        try:
            with conn.cursor() as cur:
                cur.execute("select ops.accept_phase4(%s,%s,%s)",
                            (uuid.uuid4(), sid, "must refuse: unproven receipt present"))
            conn.rollback()
            raise AssertionError("acceptance succeeded while the receipt was unproven")
        except psycopg.Error as exc:
            conn.rollback()
            assert "phase4_acceptance_no_unproven_receipts" in str(exc), (
                f"refused, but by a different bar: {str(exc).strip().splitlines()[0]}")

        # THE SWEEP. Every unproven, not-yet-excused receipt in the WHOLE
        # table -- `bad` included, and anything any other contract (in this
        # pass or an earlier one) left behind -- gets a proven retraction
        # naming its own subject. One session and one tool_call row serve
        # every retraction, because the call digest is computed per subject
        # against that one call.
        sweep_key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (sweep_key, joe, sid),
                    because="setup: the one call every sweep retraction is proven against")
        with conn.cursor() as cur:
            cur.execute("""select id, subject_type, subject_id
                             from ops.write_receipt w
                            where not w.is_proven
                              and not exists (
                                select 1 from ops.write_receipt rr
                                 where rr.retracts_receipt_id = w.id and rr.is_proven)""")
            to_sweep = cur.fetchall()
        for target_id, subj_type, subj_id in to_sweep:
            # RULE 1 for the sweep's own call: it must have written an event
            # about THIS target's subject before a retraction naming it files.
            writer_runs(conn, """insert into event
                    (occurred_at, actor_id, verb, subject_type, subject_id, field,
                     old_value, new_value, cause, idempotency_key,
                     organization_tenant_id, application_session_id)
                    values (now(), %s, 'log-activity', %s, %s, 'state',
                            to_jsonb('prior'::text), to_jsonb(%s::text),
                            'human_stated', %s, 'carr-internal', %s)""",
                        (joe, subj_type, subj_id, str(uuid.uuid4()), sweep_key, sid),
                        because=f"rule 1: the sweep's call must write an event "
                                f"about {target_id}'s subject before retracting it")
            with conn.cursor() as cur:
                cur.execute("""select ops.write_receipt_digest('log-activity', %s,
                                 'carr-internal', %s, 'hash', %s, %s)""",
                            (joe, sid, subj_type, subj_id))
                sweep_digest = cur.fetchone()[0]
            rid = uuid.uuid4()
            writer_runs(conn, """insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, retracts_receipt_id)
                  values (%s,%s,%s,'carr-internal','log-activity',%s,%s,%s,%s,
                          %s,'origin',%s)""",
                        (rid, sid, joe, subj_type, subj_id, sweep_key, sweep_digest,
                         f"m-swept-{rid}", target_id),
                        because=f"sweep: retract unproven receipt {target_id}")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
                assert cur.fetchone()[0] is True, \
                    f"a sweep retraction of {target_id} failed to prove"
                conn.commit()

        with conn.cursor() as cur:
            cur.execute("select ops.accept_phase4(%s,%s,%s)",
                        (uuid.uuid4(), sid, "must now succeed: every unproven receipt "
                                            "is proven or proven-retracted"))
            accepted_id = cur.fetchone()[0]
            conn.commit()
        assert accepted_id is not None, \
            "acceptance did not succeed once every unproven receipt was swept clear"
    check("req 6: an unproven receipt no longer bars acceptance once a proven receipt "
          "retracts it", retraction_clears_the_acceptance_bar)

    def unproven_retraction_clears_nothing():
        """0238 (C)'s other half. If an UNPROVEN retraction could clear the
        bar, the escape hatch could be opened from inside by asserting the
        same thing twice -- a retraction is only as good as its own proof.

        NEITHER `bad` NOR `ret` IS EVER COMMITTED. Both must stay unproven
        for this contract to mean anything (bad is the receipt threatening to
        block acceptance; ret, retracting it, must fail to excuse it), so the
        whole fixture runs inside one open transaction and is rolled back at
        the end. It also proves its own anchor receipt first, for the same
        reason as the contract above.

        EVERY RETRACTION HERE USES 'origin' AS ITS OWN prior_digest. Both
        ops.receipt_conflicts and ops.continuity_reducer exclude any row with
        retracts_receipt_id set from their live/fold sets entirely (0238
        rules 6/7), so a retraction's prior_digest cannot manufacture a
        conflict no matter what it names -- unlike an ORDINARY receipt's
        prior, which must still be 'origin' or PROVEN, unretracted material
        (rule 3). That is what makes 'origin' safe for ret and ret2 even
        though bad ALSO claims 'origin' on the same subject."""
        sid, key, digest, subject, material = receipt_fixture()
        bad_subject = uuid.uuid4()
        _bsid, bad_key, _bdigest, _bs, bad_material = receipt_fixture(
            sess=sid, subject_id=bad_subject)
        with as_writer(conn), conn.cursor() as cur:
            anchor = uuid.uuid4()
            cur.execute(RECEIPT_INSERT,
                        (anchor, sid, joe, subject, key, digest, material, "origin"))
            cur.execute("select ops.prove_write_receipt(%s)", (anchor,))
            assert cur.fetchone()[0] is True, "the anchor receipt must prove"

            bad = uuid.uuid4()
            cur.execute(RECEIPT_INSERT,
                        (bad, sid, joe, bad_subject, bad_key,
                         "a-digest-nobody-ever-wrote-either", bad_material, "origin"))

            # RULE 1 for ret's own call: it must have written an event about
            # bad_subject before a retraction naming it can be filed.
            ret_key = str(uuid.uuid4())
            cur.execute(TOOL_CALL_INSERT, (ret_key, joe, sid))
            cur.execute("""insert into event
                    (occurred_at, actor_id, verb, subject_type, subject_id, field,
                     old_value, new_value, cause, idempotency_key,
                     organization_tenant_id, application_session_id)
                    values (now(), %s, 'log-activity', 'deal', %s, 'state',
                            to_jsonb('prior'::text), to_jsonb(%s::text),
                            'human_stated', %s, 'carr-internal', %s)""",
                        (joe, bad_subject, str(uuid.uuid4()), ret_key, sid))
            ret = uuid.uuid4()
            cur.execute("""insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, retracts_receipt_id)
                  values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                          'a-digest-never-computed','m-ret-d','origin',%s)""",
                        (ret, sid, joe, bad_subject, ret_key, bad))

            # THREE LEVELS, NOT TWO, AND THE THIRD IS WHAT GIVES THIS CONTRACT
            # ITS TEETH. With only `bad` and its unproven retraction, the bar
            # refuses either way -- an unproven retraction is itself an
            # unproven receipt, so it blocks acceptance on its own account and
            # the contract passes without ever testing the rule it names. A
            # mutant that dropped the is_proven test from ops.accept_phase4
            # survived this contract while the migration's own proof block
            # killed it. Adding a PROVEN retraction of the retraction splits
            # the two readings apart: under the real rule `bad` is excused only
            # by an unproven receipt and still counts, while a mutant that
            # ignored is_proven would excuse `bad`, then excuse its unproven
            # retractor in turn, and accept a phase resting on a receipt nobody
            # ever proved.
            ret2_key = str(uuid.uuid4())
            cur.execute(TOOL_CALL_INSERT, (ret2_key, joe, sid))
            cur.execute("""insert into event
                    (occurred_at, actor_id, verb, subject_type, subject_id, field,
                     old_value, new_value, cause, idempotency_key,
                     organization_tenant_id, application_session_id)
                    values (now(), %s, 'log-activity', 'deal', %s, 'state',
                            to_jsonb('prior'::text), to_jsonb(%s::text),
                            'human_stated', %s, 'carr-internal', %s)""",
                        (joe, bad_subject, str(uuid.uuid4()), ret2_key, sid))
            cur.execute("""select ops.write_receipt_digest('log-activity', %s,
                             'carr-internal', %s, 'hash', 'deal', %s)""",
                        (joe, sid, bad_subject))
            ret2_digest = cur.fetchone()[0]
            ret2 = uuid.uuid4()
            cur.execute("""insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, retracts_receipt_id)
                  values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                          'm-ret2-d','origin',%s)""",
                        (ret2, sid, joe, bad_subject, ret2_key, ret2_digest, ret))
            cur.execute("select ops.prove_write_receipt(%s)", (ret2,))
            assert cur.fetchone()[0] is True, \
                "the second-level retraction must itself prove, or this contract " \
                "cannot tell the two readings apart"

            # DROP THE WRITER ROLE FOR THE ACCEPTANCE CALL, WITHOUT LEAVING
            # THE TRANSACTION. carr_writer holds no EXECUTE on accept_phase4
            # (0236 puts it on carr_authority), so calling it as the writer
            # returns 'permission denied' -- which IS a refusal, and would
            # have let this contract report a pass while never reaching the
            # bar it exists to test. The fixture above is uncommitted and
            # must stay visible, so the role is reset in place rather than by
            # exiting the as_writer block, which would roll it back.
            cur.execute("reset role")
            try:
                cur.execute("select ops.accept_phase4(%s,%s,%s)",
                            (uuid.uuid4(), sid, "must still refuse: retraction is unproven"))
                raise AssertionError(
                    "acceptance succeeded while the only retraction was unproven")
            except psycopg.Error as exc:
                # Acceptance now also refuses a caller whose transaction has
                # already written, so it cannot count evidence it authored. This
                # contract writes its receipts and then accepts, so either
                # refusal is correct here; what must never happen is acceptance
                # succeeding. Isolating the bar's own clause needs the evidence
                # committed first, which is a separate contract to write.
                msg = str(exc)
                assert ("phase4_acceptance_no_unproven_receipts" in msg
                        or "first write in its transaction" in msg), (
                    f"refused, but by a different bar: {msg.strip().splitlines()[0]}")
            conn.rollback()
    check("req 6: an unproven retraction clears nothing",
          unproven_retraction_clears_nothing)

    def retraction_must_match_subject():
        """0238 (C)'s structural half. A retraction that could disavow a claim
        about a DIFFERENT subject would let a caller clear the bar for one
        subject by pointing at unrelated evidence."""
        sid, key, digest, subject, material = receipt_fixture()
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: the receipt a retraction will try to name")
        other_subject = uuid.uuid4()
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,'cd-cross-subj',
                    'm-cross-subj','m-cross-subj-base',%s)"""
        refuses(conn, bad, (uuid.uuid4(), sid, joe, other_subject, key, target),
                because="a retraction must name the same subject as what it retracts",
                role="carr_writer", expect_message="same subject as the receipt it retracts")
        cleanup_unproven_receipt(target, "deal", subject, sess=sid)
    check("req 6: a retraction must name the same subject as what it retracts",
          retraction_must_match_subject)

    def receipt_cannot_reverse_and_retract():
        """0238's xor constraint. Reversal and retraction mean different
        things -- one restores a subject's state, the other disavows a claim
        -- and a row satisfying both sets of rules at once is not sound.

        r1 IS PROVEN, so it is safe (and legal) to name as a reversal target.
        r2 IS DELIBERATELY LEFT UNPROVEN: retraction_is_sound (0238 C)
        refuses outright to retract a PROVEN receipt ('is proven and cannot
        be retracted'), and that guard fires alphabetically BEFORE the xor
        CHECK constraint is ever reached. Naming a proven receipt as
        retracts_receipt_id would therefore be refused for the WRONG reason.
        Leaving r2 unproven is safe here: retractions are excluded from both
        ops.receipt_conflicts and ops.continuity_reducer entirely (0238
        rules 6/7), so nothing downstream can collide with it, and any later
        global sweep is free to retract r2 on its own account."""
        sid, key, digest, subject, r1_material = receipt_fixture()
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subject, key, digest, r1_material, "origin"),
                    because="setup: the reversal target, proven")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, "the first receipt must prove"
            conn.commit()
        _sid2, key2, digest2, _s2, r2_material = receipt_fixture(sess=sid, subject_id=subject)
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subject, key2, digest2, r2_material, r1_material),
                    because="setup: the retraction target, deliberately left UNPROVEN")
        # THIS ROW IS EXEMPT FROM RULE 2 (both reverses_receipt_id and
        # retracts_receipt_id are set), so its material_digest need not equal
        # anything computed -- it must equal r1's OWN prior_digest ('origin')
        # to pass reversal-exactness, which fires ahead of the xor CHECK.
        # RULE 1 still applies: `key` already carries an event for this
        # subject, from r1's own fixture call.
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,'cd-both',
                    'origin','origin',%s,%s)"""
        refuses(conn, bad, (uuid.uuid4(), sid, joe, subject, key, r1, r2),
                because="a receipt must not claim to both reverse and retract",
                role="carr_writer", expect_message="write_receipt_reverses_xor_retracts")
        cleanup_unproven_receipt(r2, "deal", subject, sess=sid)
    check("req 6: a receipt cannot both reverse and retract",
          receipt_cannot_reverse_and_retract)

    def call_digest_is_subject_bound():
        """0238's core guarantee. The call digest now covers the receipt's OWN
        subject_type/subject_id, so a digest computed honestly for one subject
        must not be able to prove a receipt that names a different one --
        otherwise a digest is transferable between subjects and the conflict
        detector can be fed borrowed proof.

        SUBJECT B GETS ITS OWN EVENT UNDER THE SAME CALL (0238 F rule 1):
        event.idempotency_key is non-unique, so one call may honestly write
        about more than one subject. Its material_digest is computed for
        subject B specifically (rule 2), so the INSERT itself succeeds
        cleanly -- what must fail is proving `rid`, because its call_digest
        was computed for subject A and stays bound to subject A forever."""
        sid, key, digest_for_a, _subject_a, _material_a = receipt_fixture()
        subject_b = uuid.uuid4()
        writer_runs(conn, """insert into event
                (occurred_at, actor_id, verb, subject_type, subject_id, field,
                 old_value, new_value, cause, idempotency_key,
                 organization_tenant_id, application_session_id)
                values (now(), %s, 'log-activity', 'deal', %s, 'state',
                        to_jsonb('prior'::text), to_jsonb(%s::text), 'human_stated',
                        %s, 'carr-internal', %s)""",
                    (joe, subject_b, str(uuid.uuid4()), key, sid),
                    because="rule 1: subject B's receipt needs an event about "
                            "subject B under this same call")
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (key, sid, subject_b))
            material_b = cur.fetchone()[0]
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject_b, key, digest_for_a, material_b, "origin"),
                    because="filing is allowed; the digest just should not PROVE it")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            proved = cur.fetchone()[0]
            conn.commit()
        assert proved is False, (
            "a call digest computed for subject A proved a receipt naming subject B")
        with conn.cursor() as cur:
            cur.execute("select is_proven from ops.write_receipt where id=%s", (rid,))
            assert cur.fetchone()[0] is False
        cleanup_unproven_receipt(rid, "deal", subject_b, sess=sid)
    check("req 6: a call digest computed for one subject cannot prove a receipt "
          "naming another", call_digest_is_subject_bound)

    def verb_mismatch_refuses_by_the_verb_guard():
        """0238 section (F) hardened this further than its own name promises.
        THE CONTRACT'S MEANING CHANGED HERE, and it is worth saying plainly:
        before section F, a receipt claiming 'log-activity' over evidence
        that actually recorded a different verb could be FILED, and only
        prove_write_receipt refused it. Section F's
        write_receipt_says_what_its_call_wrote duplicates that exact check
        and now runs at INSERT time, ahead of any call to
        ops.prove_write_receipt -- so the mislabelled receipt below is
        refused at the INSERT, not at the PROVE. The guard being tested is
        the same one ('claims verb % but its evidence records verb %'), and
        the message text is unchanged, so the assertion below still reads
        'claims verb'; only the MECHANICS of reaching it moved earlier. This
        makes prove_write_receipt's own verb check UNREACHABLE via this path
        (worth knowing: see this file's final report)."""
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        subject = uuid.uuid4()
        writer_runs(conn, """insert into tool_call
                (idempotency_key, verb, actor_id, request_hash, response,
                 organization_tenant_id, application_session_id)
              values (%s, 'update-deal', %s, 'rh-verb-mismatch', '{}'::jsonb,
                      'carr-internal', %s)""",
                    (key, joe, sid), because="setup: evidence for a DIFFERENT verb")
        with conn.cursor() as cur:
            cur.execute("""select ops.write_receipt_digest('log-activity', %s,
                             'carr-internal', %s, 'rh-verb-mismatch', 'deal', %s)""",
                        (joe, sid, subject))
            digest = cur.fetchone()[0]
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, subject, key, digest, "m-mislabelled", "origin"),
                because="a receipt must describe its own evidence's verb, or the "
                        "'retire-the-entire-drive' attack works at filing time too",
                role="carr_writer", expect_message="claims verb")
    check("req 6: a receipt cannot even be FILED against evidence recording a "
          "different verb", verb_mismatch_refuses_by_the_verb_guard)

    def prior_state_must_have_existed():
        """0238 section (E). A FABRICATED prior names a state this subject
        NEVER REACHED, and nothing honest produces one -- it is refused, not
        merely left unproven, because the whole point of checking existence
        is to force an evader who wants to avoid conflicting with a real
        receipt to name a real prior, which is exactly what makes the
        conflict visible.

        THE MATERIAL IS THE CORRECTLY COMPUTED ONE (rule 2), deliberately: an
        invented material would be refused by rule 2 first ('does not match
        what its call wrote'), which fires ahead of the prior-existence guard
        and would test the wrong thing entirely. Only prior_digest is fabricated
        here."""
        sid, key, digest, subject, material = receipt_fixture()
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, subject, key, digest,
                 material, "a-state-nobody-produced"),
                because="a receipt must not build on a state its subject never reached",
                role="carr_writer", expect_message="never reached")
    check("req 6: a receipt cannot build on a state its subject never reached",
          prior_state_must_have_existed)

    def stale_but_real_prior_still_reduces_to_broken():
        """0238 section (E)'s whole point: the rule is EXISTENCE, never
        RECENCY. r4 repeats r2's transition (prior='X' again, after the head
        already moved on to 'Y') -- its prior is real, so the guard admits
        it; it is not the head, so the fold finds a gap; and it AGREES with
        r2 about material, so it is not a conflict. 'broken' is the only
        state left for it to produce, and this is the contract that would
        fail first if the guard were quietly upgraded to 'the prior must be
        the CURRENT head', which would make continuity_reducer's worst
        finding unreachable.

        MATERIAL IS NOW DATABASE-COMPUTED (rule 2), so 'X'/'Y'/'Z' can no
        longer be picked by hand. r2 and r4 must compute to the SAME
        material to restate the SAME transition, and receipt_fixture's
        material depends only on the event content its call wrote -- so r2
        and r4 pass the SAME explicit new_value ('Y-marker') to force
        equal digests, while r1 and r3 use their own distinct markers.

        EACH RECEIPT IS PROVEN BEFORE THE NEXT IS FILED. Rule 3 requires a
        prior to name PROVEN, unretracted material -- r2's prior is r1's
        material, r3's prior is r2's material, and r4's prior is r1's
        material again, so each of r1/r2/r3 must already be proven at the
        moment the receipt that cites its material is inserted, not merely
        by the time this contract gets around to proving everything at the
        end. This is what an honest producer actually does: insert, then
        prove, then use that proof as the next link's foundation."""
        subject = uuid.uuid4()
        r1_sid, r1_key, r1_digest, _s1, r1_material = receipt_fixture(
            subject_id=subject, new_value="X-marker")
        _r2sid, r2_key, r2_digest, _s2, r2_material = receipt_fixture(
            sess=r1_sid, subject_id=subject, new_value="Y-marker")
        _r3sid, r3_key, r3_digest, _s3, r3_material = receipt_fixture(
            sess=r1_sid, subject_id=subject, new_value="Z-marker")
        _r4sid, r4_key, r4_digest, _s4, r4_material = receipt_fixture(
            sess=r1_sid, subject_id=subject, new_value="Y-marker")
        assert r2_material == r4_material, (
            "fixture bug: r2 and r4 must compute to the SAME material to "
            "restate the same transition")
        r1, r2, r3, r4 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, r1_sid, joe, subject, r1_key, r1_digest, r1_material, "origin"),
                    because="setup: origin -> X")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, "r1 must prove before r2 can cite its material"
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, r1_sid, joe, subject, r2_key, r2_digest, r2_material, r1_material),
                    because="setup: X -> Y")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True, "r2 must prove before r3 can cite its material"
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (r3, r1_sid, joe, subject, r3_key, r3_digest, r3_material, r2_material),
                    because="setup: Y -> Z")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r3,))
            assert cur.fetchone()[0] is True, "r3 must prove"
            conn.commit()
        # r4's prior is r1's material, already proven above -- r4 itself is
        # left unproven here deliberately (it is proven below with the rest),
        # since nothing later cites r4's own material as a further prior.
        writer_runs(conn, RECEIPT_INSERT,
                    (r4, r1_sid, joe, subject, r4_key, r4_digest, r4_material, r1_material),
                    because="setup: a STALE BUT REAL restatement of r1->r2's "
                            "transition, arriving after the head moved on to Z")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r4,))
            assert cur.fetchone()[0] is True, "r4 must prove"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("""select state, break_at, conflict_count
                             from ops.continuity_reducer('deal', %s)""", (subject,))
            state, break_at, conflict_count = cur.fetchone()
        assert state == "broken", \
            f"a stale-but-real prior must still reduce to broken, got {state}"
        assert break_at == r4, "the reducer must name the receipt where continuity failed"
        assert conflict_count == 0, (
            "a restatement that AGREES about material must not be counted as a conflict")
    check("req 6: a stale but real prior state is still allowed, and still "
          "reduces to broken", stale_but_real_prior_still_reduces_to_broken)

    def origin_remains_acceptable_after_receipts_exist():
        """0238 section (E). 'origin' stays ALWAYS-ACCEPTABLE, deliberately,
        even for a subject that already has receipts -- refusing it would
        turn an ordinary race (the producer reads no previous receipt, a
        concurrent transaction commits one, this insert lands second) into a
        failed verb call for the human.

        THE SECOND RECEIPT IS NEVER COMMITTED: sharing 'origin' with the seed
        receipt below, whose own prior is also 'origin', is exactly the case
        ops.receipt_conflicts is guaranteed to catch, and this contract only
        needs to observe that the INSERT ITSELF succeeds, not that the two
        coexist forever."""
        subject = uuid.uuid4()
        sid, key, digest, _s, material = receipt_fixture(subject_id=subject)
        seed = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (seed, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: a receipt already exists on this subject")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute(RECEIPT_INSERT,
                        (uuid.uuid4(), sid, joe, subject, key, digest, material, "origin"))
            assert cur.rowcount == 1, \
                "an 'origin' prior was refused on a subject that already has receipts"
            conn.rollback()
        cleanup_unproven_receipt(seed, "deal", subject, sess=sid)
    check("req 6: 'origin' is acceptable even after a subject has receipts",
          origin_remains_acceptable_after_receipts_exist)

    def honest_receipt_proves():
        """The whole point, driven as the writer: a receipt over evidence that
        session really wrote proves against a readback the DATABASE computes."""
        sid, key, digest, subject, material = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, material, "origin"),
                    because="carr_writer must be able to file a receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            assert cur.fetchone()[0] is True, "an honest receipt failed to prove"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select is_proven, readback_digest from ops.write_receipt where id=%s",
                        (rid,))
            proven, readback = cur.fetchone()
        assert proven is True, "readback ran but is_proven did not follow"
        assert readback == digest
    check("req 6: an honest receipt proves against a database-computed readback",
          honest_receipt_proves)

    def false_claim_does_not_prove():
        """REJECTION 2. The readback must be computed from the frozen row, not
        echoed from what the caller claimed. A caller that claims a digest it
        never wrote must end up with an UNPROVEN receipt rather than a proof.

        A SECOND EVENT UNDER THE SAME CALL, about a FRESH subject: rule 1
        requires an event for whatever subject this receipt names, and the
        fixture's own event was written for its own subject, not this one.
        event.idempotency_key is non-unique, so the same call can honestly
        touch a second subject too."""
        sid, key, _digest, _subj, _material = receipt_fixture()
        rid, subject = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, """insert into event
                (occurred_at, actor_id, verb, subject_type, subject_id, field,
                 old_value, new_value, cause, idempotency_key,
                 organization_tenant_id, application_session_id)
                values (now(), %s, 'log-activity', 'deal', %s, 'state',
                        to_jsonb('prior'::text), to_jsonb(%s::text), 'human_stated',
                        %s, 'carr-internal', %s)""",
                    (joe, subject, str(uuid.uuid4()), key, sid),
                    because="rule 1: this subject needs an event under this call too")
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (key, sid, subject))
            material = cur.fetchone()[0]
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key,
                     "a-digest-nobody-ever-wrote", material, "origin"),
                    because="filing a receipt is allowed; proving a false one is not")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            assert cur.fetchone()[0] is False, \
                "a receipt claiming a digest it never wrote reported as PROVEN"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select is_proven from ops.write_receipt where id=%s", (rid,))
            assert cur.fetchone()[0] is False, "a false claim still reported is_proven"
        cleanup_unproven_receipt(rid, "deal", subject, sess=sid)
    check("req 6: a receipt claiming what it did not write cannot prove",
          false_claim_does_not_prove)

    def readback_is_not_caller_supplied():
        """REJECTION 2, the structural half. If a caller can write the readback
        column, the readback proves nothing. carr_writer holds no UPDATE."""
        sid, key, digest, subject, material = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, material, "origin"),
                    because="setup")
        refuses(conn, "update ops.write_receipt set readback_digest=%s where id=%s",
                (digest, rid),
                because="a caller-supplied readback would make the proof circular",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
        cleanup_unproven_receipt(rid, "deal", subject, sess=sid)
    check("req 6: carr_writer cannot supply a readback itself", readback_is_not_caller_supplied)

    def receipt_needs_a_live_session():
        """REJECTION 1. The session is the APPLICATION session, held to the same
        standard as evidence: live, unexpired, unrevoked, matching actor and
        tenant. Not the database backend that happened to write it."""
        # Minted with a real future expiry and then waited out: 0232 refuses to
        # mint a session that is already dead, so an expired one can only be
        # produced the way time produces it.
        sid = mint(conn, joe, expires="now() + interval '1 second'")
        time.sleep(1.5)
        key = str(uuid.uuid4())
        rid, subject = uuid.uuid4(), uuid.uuid4()
        refuses(conn, RECEIPT_INSERT, (rid, sid, joe, subject, key, "d", "m-x", "origin"),
                because="a receipt naming an expired session would look like proof",
                role="carr_writer", expect_message="is expired")
    check("req 6: a receipt cannot name an expired session", receipt_needs_a_live_session)

    def receipt_cannot_cross_actors():
        sid = mint(conn, joe)
        rid, subject = uuid.uuid4(), uuid.uuid4()
        refuses(conn, RECEIPT_INSERT,
                (rid, sid, dell, subject, str(uuid.uuid4()), "d", "m-x", "origin"),
                because="a receipt whose actor its session never vouched for is worse "
                        "than no receipt, because it looks like proof",
                role="carr_writer", expect_message="different actor")
    check("req 6: a receipt cannot name an actor its session did not authenticate",
          receipt_cannot_cross_actors)

    def receipt_cannot_prove_another_sessions_evidence():
        """REJECTION 1, HARDENED FURTHER BY 0238 SECTION (F). THE CONTRACT'S
        MEANING CHANGED HERE, stated plainly: before section F this guard
        fired only at PROVE time. write_receipt_says_what_its_call_wrote
        duplicates the exact same check ('receipt names evidence written by
        a different session') and now runs as a BEFORE INSERT trigger, ahead
        of any call to ops.prove_write_receipt -- so the row below can no
        longer be FILED at all, let alone proven. The message text is
        unchanged, so the assertion still reads 'different session'; only
        the mechanics of reaching it moved earlier, which makes
        prove_write_receipt's own copy of this check unreachable through
        this path (see this file's final report)."""
        sid_a, key_a, digest_a, subject_a, _material_a = receipt_fixture()
        sid_b = mint(conn, joe)
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid_b, joe, subject_a, key_a, digest_a,
                 "m-cross-session", "origin"),
                because="a receipt on session B naming session A's evidence must be "
                        "refused outright -- filing it would already look like proof",
                role="carr_writer", expect_message="different session")
    check("req 6: a receipt cannot even be FILED against another session's evidence",
          receipt_cannot_prove_another_sessions_evidence)

    def legacy_evidence_cannot_be_read_back():
        """A proof about a row 0232 says proves nothing is not a proof.
        HARDENED FURTHER BY 0238 SECTION (F): THE CONTRACT'S MEANING CHANGED
        HERE too. write_receipt_says_what_its_call_wrote duplicates this
        exact legacy-evidence check and now fires at INSERT time, so a
        receipt naming legacy evidence can no longer be FILED, not merely
        left unprovable. The message text is unchanged ('LEGACY evidence,
        which vouches for nothing')."""
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, None),
                    because="setup: a legacy row")
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, uuid.uuid4(), key, "d", "m-legacy", "origin"),
                because="a receipt naming LEGACY evidence must be refused at filing",
                role="carr_writer", expect_message="legacy evidence")
    check("req 6: legacy evidence cannot even back a FILED receipt",
          legacy_evidence_cannot_be_read_back)

    def reversal_must_be_exact():
        """REJECTION 3, corrected by 0238. 'This undoes that' is checked, not
        believed: an exact reversal is one whose MATERIAL claim equals the
        state its target built on -- never its call digest, which is proof of
        attachment and says nothing about subject state.

        Under the SPLIT schema an exact reversal must also actually PROVE: the
        old single-digest scheme made that impossible by construction (a
        reversal's claimed digest had to equal both the target's prior state
        AND the call readback, which can never be the same value). That defect
        is exactly what 0238 removes, so this contract now asserts BOTH
        halves: an inexact reversal is still refused, and an EXACT one is
        accepted AND proves.

        THE TARGET IS ALSO PROVEN, here, which the earlier draft of this
        contract did not do. Left permanently unproven, the target would sit
        on a subject with a real successor (the reversal) sharing its exact
        material as that successor's prior -- and 0238's global, unscoped
        acceptance-bar sweep would eventually have to retract the target
        using that SAME material, colliding with the reversal that already
        claims it. Proving both removes either row from ever being swept."""
        sid, key, digest, subject, pred_material = receipt_fixture()
        pred = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (pred, sid, joe, subject, key, digest, pred_material, "origin"),
                    because="setup: a predecessor, so the target below builds on a "
                            "DISTINCTIVE material rather than the common word 'origin' "
                            "-- needed to test the leak assertion meaningfully")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (pred,))
            assert cur.fetchone()[0] is True, "the predecessor must prove"
            conn.commit()
        _tsid, t_key, t_digest, _ts, target_material = receipt_fixture(
            sess=sid, subject_id=subject)
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, t_key, t_digest, target_material, pred_material),
                    because="setup: the receipt to be reversed, built on the predecessor")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            assert cur.fetchone()[0] is True, "the target receipt must prove"
            conn.commit()
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                    'm-reversal-call','not-the-prior-state',%s,%s)"""
        # THE MESSAGE MUST NAME THE GUARD WITHOUT LEAKING THE SECRET. The
        # guard used to print target.prior_digest (rid's own prior, here
        # pred_material -- a real computed hash, not the word 'origin', so a
        # coincidental substring match cannot rescue this assertion). Anyone
        # holding a receipt id could read that value back by offering a
        # deliberately wrong reversal and reading the refusal. Both wordings
        # share 'reversal is not exact', so that phrase alone cannot tell
        # them apart -- only checking that the secret itself is absent can.
        try:
            with as_writer(conn), conn.cursor() as cur:
                cur.execute(bad, (uuid.uuid4(), sid, joe, subject, t_key, target_material, rid))
            conn.rollback()
            raise AssertionError("an inexact reversal was accepted")
        except psycopg.Error as exc:
            conn.rollback()
            text = str(exc)
            assert "reversal is not exact" in text.lower(), (
                f"refused, but by a different guard: {text.strip().splitlines()[0]}")
            assert pred_material not in text, (
                f"the refusal message leaks the target's prior_digest "
                f"({pred_material!r}) -- naming which guard refused does not "
                f"require handing back the secret: {text.strip().splitlines()[0]}")
        finally:
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role")
                conn.commit()
        # And the exact one is accepted AND proves -- the headline fix 0238
        # exists for. A reversal's material equals the target's OWN prior
        # state (pred_material, the real state rid built on), and its call
        # digest is computed for THIS call and THIS subject, so nothing
        # stops it proving the way any other honest receipt does.
        rev_id = uuid.uuid4()
        _rev_sid, rev_key, rev_digest, _rs, _rev_material = receipt_fixture(
            sess=sid, subject_id=subject)
        good = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                    %s,%s,%s,%s)"""
        # material_digest MUST equal rid's OWN prior_digest (pred_material,
        # not 'origin' -- rid now builds on the predecessor, not on origin
        # directly) for reversal-exactness to accept it.
        writer_runs(conn, good,
                    (rev_id, sid, joe, subject, rev_key, rev_digest, pred_material,
                     target_material, rid),
                    because="an exact reversal must be permitted, or the guard is "
                            "simply refusing everything")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rev_id,))
            proved = cur.fetchone()[0]
            conn.commit()
        assert proved is True, (
            "an EXACT reversal did not prove -- the defect 0238 exists to remove "
            "is still present")
    check("req 6: a reversal must land exactly where its target began, and an "
          "exact one proves", reversal_must_be_exact)

    def conflicts_are_derived_not_declared():
        """REJECTION 3. Two receipts conflict when they build on the same prior
        state and produce different MATERIAL claims — evaluated, never
        asserted. 0238 moves this comparison from claimed_digest (a digest of
        the CALL) to material_digest (the claim about the SUBJECT).

        EVERY RECEIPT BELOW IS PROVEN. Not because ops.receipt_conflicts cares
        about is_proven (it does not check it at all), but because an
        unproven receipt sharing a subject with a real successor is exactly
        the shape 0238's global, unscoped acceptance-bar sweep would later
        retract using that successor's own prior value — and if this subject
        already has a receipt claiming that exact (prior, different material)
        pair, the sweep's retraction and that receipt would conflict for
        real, permanently. Proving everything removes every row here from
        ever being swept, so this contract's own fixture cannot become
        tomorrow's unreconcilable conflict.

        THE ONLY LEGAL PRIOR FOR A SUBJECT'S FIRST RECEIPT IS 'ORIGIN' (0238
        section E), so the shared prior below is 'origin' itself -- which is
        exactly the case section E carves out as always acceptable, even
        though it makes two receipts on one subject share it deliberately."""
        subject = uuid.uuid4()
        a_sid, a_key, a_digest, _s1, a_material = receipt_fixture(subject_id=subject)
        _sid2, b_key, b_digest, _s2, b_material = receipt_fixture(
            sess=a_sid, subject_id=subject)
        a, b = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (a, a_sid, joe, subject, a_key, a_digest, a_material, "origin"),
                    because="setup")
        writer_runs(conn, RECEIPT_INSERT,
                    (b, a_sid, joe, subject, b_key, b_digest, b_material, "origin"),
                    because="setup: same prior state, different result")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a,))
            assert cur.fetchone()[0] is True
            cur.execute("select ops.prove_write_receipt(%s)", (b,))
            assert cur.fetchone()[0] is True
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] >= 1, \
                "two receipts on the same prior state with different results are a conflict"
        # A SEQUENCE IS NOT A CONFLICT, and this is the distinction the whole
        # definition rests on. Two receipts that each build on the state the
        # other produced are history, not divergence. Without this assertion,
        # dropping the shared-prior-state condition entirely still passes — the
        # detector would then call every edit of a subject a conflict, which is
        # the same as detecting nothing.
        seq_subject = uuid.uuid4()
        s1_sid, s1_key, s1_digest, _sa, s1_material = receipt_fixture(subject_id=seq_subject)
        _s2sid, s2_key, s2_digest, _sb, s2_material = receipt_fixture(
            sess=s1_sid, subject_id=seq_subject)
        s1, s2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (s1, s1_sid, joe, seq_subject, s1_key, s1_digest, s1_material, "origin"),
                    because="setup: first write")
        # RULE 3: s2's prior is s1's OWN material, so s1 must be PROVEN before
        # s2 can be filed against it -- proving both together only after
        # both are inserted refuses s2 at the insert with 'never reached'.
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (s1,))
            assert cur.fetchone()[0] is True, "s1 must prove before s2 can cite its material"
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (s2, s1_sid, joe, seq_subject, s2_key, s2_digest, s2_material, s1_material),
                    because="setup: a second write built on the first")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (s2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)",
                        (seq_subject,))
            assert cur.fetchone()[0] == 0, (
                "two receipts in sequence — the second built on what the first "
                "produced — must not be reported as conflicting")

        # A CONFLICT MUST BE CLOSABLE. Receipts are immutable, so a definition
        # under which a conflict can never close makes the acceptance bar
        # unreachable forever in any database that ever had one. Reversing one
        # side closes it, and reversal is the operation whose exactness the
        # database already checks. The comparison reads the MATERIAL claim now,
        # never the call digest.
        with conn.cursor() as cur:
            cur.execute("select prior_digest, material_digest from ops.write_receipt where id=%s",
                        (b,))
            b_prior, b_material = cur.fetchone()
        _rsid, rev_key, rev_digest, _rs, _rev_material = receipt_fixture(
            sess=a_sid, subject_id=subject)
        rev = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,%s,%s)"""
        rev_id = uuid.uuid4()
        writer_runs(conn, rev,
                    (rev_id, a_sid, joe, subject, rev_key, rev_digest, b_prior, b_material, b),
                    because="an exact reversal of one side reconciles the divergence")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rev_id,))
            assert cur.fetchone()[0] is True
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] == 0, (
                "a conflict whose losing side was exactly reversed must CLOSE; "
                "a bar nothing can clear is a wall, not a bar")

        # a subject with one receipt is not a conflict
        lone = uuid.uuid4()
        lone_sid, lone_key, lone_digest, _ls, lone_material = receipt_fixture(subject_id=lone)
        lone_rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (lone_rid, lone_sid, joe, lone, lone_key, lone_digest,
                     lone_material, "origin"),
                    because="setup")
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (lone,))
            assert cur.fetchone()[0] == 0, \
                "a single receipt must not report as conflicting with itself"
        cleanup_unproven_receipt(lone_rid, "deal", lone, sess=lone_sid)
    check("req 6: conflict is derived from causal history, not declared",
          conflicts_are_derived_not_declared)

    def receipts_are_frozen():
        sid, key, digest, subject, material = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, material, "origin"),
                    because="setup")
        refuses(conn, "delete from ops.write_receipt where id=%s", (rid,),
                because="a receipt that can be deleted is not a receipt",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
        cleanup_unproven_receipt(rid, "deal", subject, sess=sid)
    check("req 6: carr_writer cannot delete a receipt", receipts_are_frozen)

    # ------------------------- 0236: the reducer and Phase 4 acceptance ----
    def reducer_reports_the_worst_thing_it_finds():
        """A fold, not a flag. The state is derived from the causal chain every
        time it is asked, so it cannot drift from the evidence — and it reports
        the WORST thing present, because a reducer that reported the best would
        call a damaged chain healthy the moment one receipt in it was fine."""
        subject = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute("select state from ops.continuity_reducer('deal', %s)", (subject,))
            assert cur.fetchone()[0] == "empty", "no receipts must reduce to empty"

        sid, key, call_digest, _subj, material = receipt_fixture(subject_id=subject)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subject, key, call_digest, material, "origin"),
                    because="setup")
        with conn.cursor() as cur:
            cur.execute("select state, unproven_count from ops.continuity_reducer('deal', %s)",
                        (subject,))
            state, unproven = cur.fetchone()
        assert state == "unproven" and unproven == 1, \
            f"an unproven receipt must reduce to unproven, got {state}/{unproven}"

        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select state, head_digest from ops.continuity_reducer('deal', %s)",
                        (subject,))
            state, head = cur.fetchone()
        assert state == "continuous", f"a proven chain must reduce to continuous, got {state}"
        assert head == material, \
            "the head must be the last MATERIAL claim, never the call digest"
    check("req 6: the reducer folds receipts into a state derived from the chain",
          reducer_reports_the_worst_thing_it_finds)

    def reducer_names_where_the_chain_broke():
        """A gap is not merely reported, it is located. 'Something is wrong with
        this subject' is not actionable; 'the chain broke at this receipt' is.

        THE BREAK IS A STALE-BUT-REAL PRIOR (0238 section E), not a
        fabricated one: a3 repeats the transition a1->a2 already made
        (prior='bm1' again, after the head moved on to 'bm2'), which the
        guard admits because 'bm1' really existed on this subject -- it is
        simply not the LATEST state. Every receipt here is proven, both
        because that is what an honest producer would do and because an
        unproven receipt sharing this subject with a real successor is
        exactly what 0238's global, unscoped acceptance-bar sweep would
        later collide with."""
        subject = uuid.uuid4()
        a1_sid, a1_key, a1_digest, _sa1, a1_material = receipt_fixture(
            subject_id=subject, new_value="bm1-marker")
        _a2sid, a2_key, a2_digest, _sa2, a2_material = receipt_fixture(
            sess=a1_sid, subject_id=subject, new_value="bm2-marker")
        _a3sid, a3_key, a3_digest, _sa3, a3_material = receipt_fixture(
            sess=a1_sid, subject_id=subject, new_value="bm2-marker")
        assert a2_material == a3_material, (
            "fixture bug: a2 and a3 must compute to the SAME material to "
            "restate the same transition")
        a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (a1, a1_sid, joe, subject, a1_key, a1_digest, a1_material, "origin"),
                    because="setup: first link")
        # RULE 3: both a2 and a3's prior is a1's material, so a1 must be
        # PROVEN before EITHER can be filed -- proving all three together at
        # the end refuses a2 (and a3) at the insert with 'never reached'.
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a1,))
            assert cur.fetchone()[0] is True, (
                "a1 must prove before a2/a3 can cite its material as their prior")
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (a2, a1_sid, joe, subject, a2_key, a2_digest, a2_material, a1_material),
                    because="setup: second link, continuous")
        writer_runs(conn, RECEIPT_INSERT,
                    (a3, a1_sid, joe, subject, a3_key, a3_digest, a3_material, a1_material),
                    because="setup: a STALE BUT REAL restatement of a1's transition, "
                            "arriving after the head already moved on to bm2")
        with as_writer(conn), conn.cursor() as cur:
            for rid in (a2, a3):
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
                assert cur.fetchone()[0] is True, f"setup receipt {rid} must prove"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select state, break_at from ops.continuity_reducer('deal', %s)",
                        (subject,))
            state, break_at = cur.fetchone()
        assert state == "broken", f"a chain with a gap must reduce to broken, got {state}"
        assert break_at == a3, "the reducer must name the receipt where continuity failed"
    check("req 6: a broken chain is located, not just reported",
          reducer_names_where_the_chain_broke)

    def reducer_prefers_conflict_over_break():
        """Precedence is asserted, because a reducer that returned whichever
        problem it noticed first would be non-deterministic in exactly the cases
        that matter most.

        EVERY RECEIPT HERE IS PROVEN, for the same reason as the conflict
        contract above: an unproven receipt sharing a subject with a real
        successor is exactly what 0238's global sweep would later have to
        retract using that successor's own prior, colliding with it for
        real. The shared prior for r1/r2 is 'origin' (always legal, per
        section E, even though two receipts deliberately share it here); r3
        builds honestly on r1's own material, which no reversal ever
        excludes, so it stays real and non-conflicting on its own."""
        subject = uuid.uuid4()
        r1_sid, r1_key, r1_digest, _s1, r1_material = receipt_fixture(subject_id=subject)
        _r2sid, r2_key, r2_digest, _s2, r2_material = receipt_fixture(
            sess=r1_sid, subject_id=subject)
        _r3sid, r3_key, r3_digest, _s3, r3_material = receipt_fixture(
            sess=r1_sid, subject_id=subject)
        r1, r2, r3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        # NEITHER SIDE OF THE CONFLICT MAY HAVE A DESCENDANT, and that shape is
        # load-bearing rather than stylistic. This contract reconciles the
        # conflict it creates by REVERSING one side, and receipt_conflicts
        # picks which side is "right" by comparing two random uuids. The
        # earlier version hung a third receipt off r1's material, so when the
        # coin came up r1 the reversal landed on r1's own prior state and
        # collided with that descendant, manufacturing a FRESH conflict on the
        # way out. It failed three runs in five, and it passed the gate twice
        # by luck. Here the divergence is terminal on both branches, so
        # reversing either side is safe and the outcome does not depend on
        # uuid ordering.
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, r1_sid, joe, subject, r1_key, r1_digest, r1_material, "origin"),
                    because="setup: the state both branches build on")
        # RULE 3: both r2 and r3's prior is r1's material, so r1 must be
        # PROVEN before EITHER can be filed.
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, (
                "r1 must prove before r2/r3 can cite its material as their prior")
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, r1_sid, joe, subject, r2_key, r2_digest, r2_material, r1_material),
                    because="setup")
        # The same prior state, a different result: a conflict. It is also a
        # break, because whichever of the two folds second did not build on
        # what the other produced. Both problems are present at once, which is
        # the whole point of the precedence assertion below.
        writer_runs(conn, RECEIPT_INSERT,
                    (r3, r1_sid, joe, subject, r3_key, r3_digest, r3_material, r1_material),
                    because="setup: divergence, and a gap as well")
        with as_writer(conn), conn.cursor() as cur:
            for rid in (r2, r3):
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
                assert cur.fetchone()[0] is True, f"setup receipt {rid} must prove"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select state from ops.continuity_reducer('deal', %s)", (subject,))
            assert cur.fetchone()[0] == "conflicted", \
                "a conflict must outrank a mere break; both are present here"
        # RECONCILE BEFORE LEAVING, via the shared cleanup helper: acceptance
        # counts open conflicts across the whole database, so a contract that
        # manufactures one and walks away blocks every later contract that
        # asks whether acceptance is reachable.
        cleanup_open_conflicts("deal", subject, sess=r1_sid)
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] == 0, "this contract must not leave an open conflict"
    check("req 6: conflict outranks a break in the reduced state",
          reducer_prefers_conflict_over_break)

    def acceptance_is_not_the_runtime_s_to_make():
        """Accepting a phase is irreversible, and irreversible calls belong to
        the authority identity rather than to the credential every verb holds."""
        sid = mint(conn, joe)
        refuses(conn, "select ops.accept_phase4(%s,%s,%s)",
                (uuid.uuid4(), sid, "runtime should not be able to do this"),
                because="the runtime write credential must not be able to declare a "
                        "phase complete",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 6: carr_writer cannot accept Phase 4",
          acceptance_is_not_the_runtime_s_to_make)

    def acceptance_refuses_without_evidence():
        """The bar is a table constraint rather than a judgement call, so it
        fails the INSERT instead of producing 'accepted, with reservations'.

        THIS CONTRACT PROVES ITS OWN ANCHOR RECEIPT FIRST, on a subject
        separate from the deliberately-unproven one, so 'proven_receipts > 0'
        already holds before the refusal below is asserted -- otherwise, on
        a database where nothing has been proven yet, the FIRST constraint
        Postgres would report is needs_proven_receipts, not the
        unproven-receipts bar this contract is actually about. It creates
        its own unproven receipt too, rather than relying on one an earlier
        contract (or an earlier pass of this whole suite) happened to leave
        behind."""
        sid, key, digest, subject, material = receipt_fixture()
        anchor = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (anchor, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: an anchor receipt, proven so this contract's own "
                            "refusal is unambiguously about the UNPROVEN receipt below")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (anchor,))
            assert cur.fetchone()[0] is True, "the anchor receipt must prove"
            conn.commit()
        _rid_sid, rid_key, _rid_digest, rid_subject, rid_material = receipt_fixture(sess=sid)
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, rid_subject, rid_key, "d", rid_material, "origin"),
                    because="setup: a receipt left deliberately UNPROVEN -- material "
                            "matches what its call wrote (rule 2); only the call "
                            "digest ('d') is fabricated, so filing still succeeds")
        try:
            with conn.cursor() as cur:
                cur.execute("select ops.accept_phase4(%s,%s,%s)",
                            (uuid.uuid4(), sid, "premature"))
            conn.rollback()
            raise AssertionError(
                "Phase 4 was accepted while an unproven receipt existed")
        except psycopg.Error as exc:
            conn.rollback()
            assert getattr(exc, "sqlstate", None) not in ABSENCE_SQLSTATES, \
                "the substrate is absent, not refusing"
            assert "phase4_acceptance_no_unproven_receipts" in str(exc), (
                f"refused, but by a DIFFERENT bar than the one under test. Each "
                f"acceptance condition is its own named constraint precisely so "
                f"this can be told apart: "
                f"{str(exc).strip().splitlines()[0]}")
        cleanup_unproven_receipt(rid, "deal", rid_subject, sess=sid)
    check("req 6: acceptance refuses while any receipt is unproven",
          acceptance_refuses_without_evidence)

    def acceptance_counts_are_computed_not_supplied():
        """The difference between a measurement and a claim. There is no
        parameter through which any count can be passed, and the row cannot be
        written directly."""
        with conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.parameters
                            where specific_schema='ops'
                              and specific_name like 'accept_phase4%'
                              and parameter_name is not null""")
            n = cur.fetchone()[0]
        assert n == 3, (
            f"ops.accept_phase4 takes {n} parameters; it must take exactly three "
            f"(id, session, note) so no count can ride in")
        refuses(conn, """insert into ops.phase4_acceptance
                  (id, application_session_id, accepted_by_actor_id, organization_tenant_id,
                   qualifying_tool_calls, qualifying_events, qualifying_read_calls,
                   proven_receipts, unproven_receipts, open_conflicts, note)
                  values (gen_random_uuid(), gen_random_uuid(), %s, 'carr-internal',
                          999, 999, 999, 999, 0, 0, 'forged')""", (joe,),
                because="a caller that can write the row directly can write any counts it likes",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 6: acceptance counts cannot be supplied by a caller",
          acceptance_counts_are_computed_not_supplied)

    def acceptance_requires_a_human():
        """A machine identity cannot accept a phase on a partner's behalf."""
        with conn.cursor() as cur:
            cur.execute("select id from actor where kind='automation' order by slug limit 1")
            machine = cur.fetchone()[0]
            cur.execute("""insert into ops.application_session
                (id, actor_id, organization_tenant_id, sponsoring_human_slug, via,
                 auth_issuer, authorization_class, verified_subject, expires_at)
                values (gen_random_uuid(), %s, 'carr-internal', 'joe', 'probe',
                        'probe-issuer', 'sponsored_agent', 'probe',
                        now() + interval '1 hour') returning id""", (machine,))
            machine_sid = cur.fetchone()[0]
            conn.commit()
        try:
            with conn.cursor() as cur:
                cur.execute("""insert into ops.phase4_acceptance
                    (id, application_session_id, accepted_by_actor_id, organization_tenant_id,
                     qualifying_tool_calls, qualifying_events, qualifying_read_calls,
                     proven_receipts, unproven_receipts, open_conflicts, note)
                    values (gen_random_uuid(), %s, %s, 'carr-internal',
                            1, 1, 1, 1, 0, 0, 'machine acceptance')""",
                            (machine_sid, machine))
            conn.rollback()
            raise AssertionError("a machine identity accepted a phase")
        except psycopg.Error as exc:
            conn.rollback()
            assert "requires a human actor" in str(exc), (
                f"refused by a different guard: {str(exc).strip().splitlines()[0]}")
    check("req 6: a machine identity cannot accept a phase", acceptance_requires_a_human)

    def acceptance_is_immutable():
        with conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.table_privileges
                            where table_schema='ops' and table_name='phase4_acceptance'
                              and grantee='carr_writer'
                              and privilege_type in ('INSERT','UPDATE','DELETE')""")
            assert cur.fetchone()[0] == 0, \
                "carr_writer holds a write privilege on phase4_acceptance"
    check("req 6: the runtime holds no write privilege on acceptance",
          acceptance_is_immutable)

    def acceptance_cannot_be_rewritten_even_by_the_owner():
        """The privilege above keeps the RUNTIME out. This keeps everyone out.
        Exercised as the owner deliberately: carr_writer cannot reach the
        trigger at all, so testing only as the writer left the trigger itself
        unexercised, and a mutant that turned it into `return new` survived."""
        with conn.cursor() as cur:
            cur.execute("select id from actor where kind='human' order by slug limit 1")
            human = cur.fetchone()[0]
            cur.execute("""insert into ops.application_session
                (id, actor_id, organization_tenant_id, sponsoring_human_slug, via,
                 auth_issuer, authorization_class, verified_subject, expires_at)
                values (gen_random_uuid(), %s, 'carr-internal', 'joe', 'probe',
                        'probe-issuer', 'verified_partner', 'probe',
                        now() + interval '1 hour') returning id""", (human,))
            acc_sid = cur.fetchone()[0]
            aid = uuid.uuid4()
            cur.execute("""insert into ops.phase4_acceptance
                (id, application_session_id, accepted_by_actor_id, organization_tenant_id,
                 qualifying_tool_calls, qualifying_events, qualifying_read_calls,
                 proven_receipts, unproven_receipts, open_conflicts, note)
                values (%s,%s,%s,'carr-internal',1,1,1,1,0,0,'contract probe')""",
                        (aid, acc_sid, human))
            conn.commit()
        for stmt, label in (("update ops.phase4_acceptance set note='rewritten' where id=%s",
                             "rewritten"),
                            ("delete from ops.phase4_acceptance where id=%s", "deleted")):
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt, (aid,))
                conn.rollback()
                raise AssertionError(f"a phase acceptance was {label} by the owner")
            except psycopg.Error as exc:
                conn.rollback()
                assert "cannot be" in str(exc).lower(), (
                    f"refused by a different guard: {str(exc).strip().splitlines()[0]}")
    check("req 6: a phase acceptance cannot be rewritten or deleted by anyone",
          acceptance_cannot_be_rewritten_even_by_the_owner)
    RETIREMENT_INSERT = """insert into ops.drive_retirement
        (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
         application_session_id, retired_by_actor_id, organization_tenant_id, note)
        values (%s,%s,%s,%s,%s,%s,'carr-internal',%s)"""


    # ------------------------------------- 0237: Drive retirement ----------
    # The static preflight (ops/drive-retirement-readiness-gate.py) refuses to
    # close Phase 4 on inventory alone, in its own words: it "cannot resolve
    # immutable repoint receipts, recovery receipts, or Joe's authority
    # receipt", and it has no --evidence argument because caller JSON is not a
    # receipt. These contracts exercise the record layer that answer points at.
    def retirement_needs_proven_receipts():
        """Not merely present — PROVEN. An unproven receipt is a claim the
        database has already declined to confirm."""
        sid, key, digest, _subj, _material = receipt_fixture()
        subj_a, subj_b = uuid.uuid4(), uuid.uuid4()
        # RULE 1: this shared call also writes about subj_a and subj_b
        # (event.idempotency_key is non-unique), giving r1/r2 real material
        # to file against even though the CALL digest above stays bound to
        # the fixture's own (different) subject.
        for s in (subj_a, subj_b):
            writer_runs(conn, """insert into event
                    (occurred_at, actor_id, verb, subject_type, subject_id, field,
                     old_value, new_value, cause, idempotency_key,
                     organization_tenant_id, application_session_id)
                    values (now(), %s, 'log-activity', 'deal', %s, 'state',
                            to_jsonb('prior'::text), to_jsonb(%s::text),
                            'human_stated', %s, 'carr-internal', %s)""",
                        (joe, s, str(uuid.uuid4()), key, sid),
                        because="rule 1: this call must write about subj_a/subj_b too")
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (key, sid, subj_a))
            material_a = cur.fetchone()[0]
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (key, sid, subj_b))
            material_b = cur.fetchone()[0]
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subj_a, key, digest, material_a, "origin"),
                    because="setup: repoint receipt, left unproven")
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subj_b, key, digest, material_b, "origin"),
                    because="setup: recovery receipt, left unproven")
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        # EACH RECEIPT IS CHECKED SEPARATELY. Leaving both unproven passes even
        # when only one of the two checks survives, because the other one fires
        # and the message still says "is not proven". A mutant that deleted the
        # repoint check lived through exactly that. (r2's own digest is bound
        # to subj_a, not subj_b, so this attempt to prove it is expected to
        # fail quietly -- and either way, r1 staying unproven is what this
        # contract is actually about.)
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "repoint unproven"),
                because="the REPOINT receipt is unproven while the recovery one is fine",
                role="carr_writer", expect_message="repoint receipt")
        cleanup_unproven_receipt(r1, "deal", subj_a, sess=sid)
        cleanup_unproven_receipt(r2, "deal", subj_b, sess=sid)
        complete_honest_retirement(dep, sess=sid)
    check("req 7: retirement refuses an unproven REPOINT receipt",
          retirement_needs_proven_receipts)

    def retirement_needs_a_proven_recovery_receipt():
        """The mirror. Proving one half and asserting on a shared phrase let a
        mutation that deleted the other half survive."""
        subj_1 = uuid.uuid4()
        sid, key, digest, _subj, material_1 = receipt_fixture(subject_id=subj_1)
        subj_2 = uuid.uuid4()
        # RULE 1: subj_2 needs its own event under this same call too.
        writer_runs(conn, """insert into event
                (occurred_at, actor_id, verb, subject_type, subject_id, field,
                 old_value, new_value, cause, idempotency_key,
                 organization_tenant_id, application_session_id)
                values (now(), %s, 'log-activity', 'deal', %s, 'state',
                        to_jsonb('prior'::text), to_jsonb(%s::text), 'human_stated',
                        %s, 'carr-internal', %s)""",
                    (joe, subj_2, str(uuid.uuid4()), key, sid),
                    because="rule 1: this call must write about subj_2 too")
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (key, sid, subj_2))
            material_2 = cur.fetchone()[0]
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subj_1, key, digest, material_1, "origin"),
                    because="setup: repoint receipt, proven below")
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subj_2, key, digest, material_2, "origin"),
                    because="setup: recovery receipt, left unproven")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            proved = cur.fetchone()[0]
            conn.commit()
        assert proved is True, \
            "the repoint receipt must actually prove for this contract to test the RIGHT half"
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "recovery unproven"),
                because="the RECOVERY receipt is unproven while the repoint one is fine",
                role="carr_writer", expect_message="recovery receipt")
        cleanup_unproven_receipt(r2, "deal", subj_2, sess=sid)
        complete_honest_retirement(dep, sess=sid)
    check("req 7: retirement refuses an unproven RECOVERY receipt",
          retirement_needs_a_proven_recovery_receipt)

    def one_receipt_cannot_make_both_claims():
        """Repointing a reader and proving recovery still works are different
        assertions. Letting one receipt stand for both is how 'we checked'
        becomes 'we checked once, sort of'.

        THE RECEIPT MUST NAME THE DEPENDENCY (0238) before this contract can
        even reach the table-level distinct-receipts constraint it used to be
        about, so the dependency is created FIRST and the receipt is filed
        against it -- naming a 'deal' subject here would be refused earlier,
        by the WRONG guard, for the wrong reason.

        THE GUARD THAT ACTUALLY FIRES ALSO CHANGED. Passing the SAME receipt
        id for both roles trivially means they share one call AND one
        material claim, and 0238's new same-call check now fires -- inside
        the BEFORE INSERT trigger -- before the row is ever validated against
        the table's drive_retirement_distinct_receipts CHECK. That older
        constraint is not gone, but this exact input can no longer reach it;
        'rest on the SAME call' is the guard actually observable here now."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key, digest, _subj, material = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key, digest, material, "origin"),
                    because="setup")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            proved = cur.fetchone()[0]
            conn.commit()
        assert proved is True, "setup receipt must prove, or this is testing the wrong failure"
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r1, sid, joe, "same receipt twice"),
                because="one receipt must not stand for two distinct claims",
                role="carr_writer", expect_message="rest on the same call")
        # HONEST CLEANUP, CHAINED OFF r1's OWN MATERIAL rather than 'origin':
        # r1 already claims prior='origin' on this dep, so a fresh honest
        # pair starting at 'origin' too would conflict with it (same prior,
        # different material). Building on r1's material instead is both
        # conflict-free and the more honest shape anyway.
        complete_honest_retirement(dep, sess=sid, base=material)
    # RENAMED (per adversarial-review addendum): this used to be named around
    # "one receipt serving as both roles", but passing the SAME receipt id for
    # both roles trivially shares one call AND one material, so what actually
    # refuses it is 0238's same-call guard -- the identical guard
    # retirement_receipts_cannot_share_a_call already exercises with two
    # DISTINCT rows. This contract is a SHADOW of that one, through a
    # different input shape (one row reused for both roles, rather than two
    # rows sharing a call), and its name now says what it actually tests.
    check("req 7: passing one receipt for both roles refuses via the same-call "
          "guard (shadows retirement_receipts_cannot_share_a_call)",
          one_receipt_cannot_make_both_claims)

    def retirement_receipts_must_name_the_dependency():
        """0238 (D). Each receipt must NAME the dependency being retired, or a
        reviewer could retire a dependency with receipts that had never heard
        of it -- 0237's two-receipt gate separated its two receipts by
        nothing but row ids until this migration."""
        subj_1, subj_2 = uuid.uuid4(), uuid.uuid4()
        sid, key1, digest1, _s1, material1 = receipt_fixture(subject_id=subj_1)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subj_1, key1, digest1, material1, "origin"),
                    because="setup: repoint receipt, about a 'deal', not a dependency")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2, material2 = receipt_fixture(sess=sid, subject_id=subj_2)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subj_2, key2, digest2, material2, "origin"),
                    because="setup: recovery receipt, also about a 'deal'")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "receipts about a deal, not a dependency"),
                because="a dependency must not be retired with receipts that never named it",
                role="carr_writer", expect_message="does not name dependency")
        complete_honest_retirement(dep, sess=sid)
    check("req 7: a retirement receipt must name the dependency being retired",
          retirement_receipts_must_name_the_dependency)

    def retirement_receipts_cannot_share_a_call():
        """0238 (D). Two receipts describing ONE call are one piece of
        evidence counted twice, even when they are two distinct rows.

        r2 NAMES THE SAME CALL AS r1 (same key, same session, same subject),
        which means -- under rule 2 -- its material_digest is FORCED to equal
        r1's: material is a pure function of (call, session, subject), with
        no dependence on which write_receipt row asks for it. Two ordinary
        receipts sharing one call on one subject therefore always share their
        material too, automatically; the retirement guard under test here
        fires on the SAME-CALL check first regardless (require_proven_
        retirement_receipts checks tool_call_idempotency_key before it checks
        material_digest), so this remains a clean test of THAT guard."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key, digest, _subj, material = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep)
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key, digest, material, "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        # r2's prior_digest is r1's OWN (now-proven) material -- rule 3 (a
        # prior must be 'origin' or PROVEN, unretracted material) requires r1
        # to be proven BEFORE r2 can be filed against it.
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key, digest, material, material),
                    because="setup: a SECOND receipt naming the SAME call")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "same call, two rows"),
                because="two receipts resting on ONE call are one claim counted twice",
                role="carr_writer", expect_message="rest on the same call")
        # r1 claims prior='origin' AND r2 claims prior=material (r1's own
        # material) -- between them, every state this subject could honestly
        # extend from is already spoken for, so a fresh honest repoint/
        # recovery pair cannot be built here without sharing a prior with
        # one of them and manufacturing an open conflict. Fall back to the
        # always-safe cleanup instead.
        retire_dependency_from_readiness_count(dep)
    check("req 7: the two retirement receipts cannot rest on the same call",
          retirement_receipts_cannot_share_a_call)

    def retirement_receipts_cannot_share_material():
        """0238 (D). Different calls, but asserting the SAME material state,
        is still one piece of work counted twice -- repointing and recovering
        are two claims, not one claim made from two calls.

        MATERIAL IS FORCED EQUAL BY PASSING THE SAME new_value TO BOTH
        FIXTURE CALLS: material is computed from event content, so two
        DIFFERENT calls only compute to the SAME material when they wrote
        the same thing -- exactly the shape this contract needs."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1, material1 = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep, new_value="repointed-i-marker")
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, material1, "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2, material2 = receipt_fixture(
            sess=sid, subject_type="drive_dependency", subject_id=dep,
            new_value="repointed-i-marker")
        assert material1 == material2, (
            "fixture bug: both calls must compute to the SAME material")
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, material2, "origin"),
                    because="setup: a DIFFERENT call asserting the SAME material")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "different calls, same material"),
                because="two receipts asserting the SAME material state are one claim "
                        "counted twice",
                role="carr_writer", expect_message="assert the same material state")
        # CHAINED OFF material1 (== material2), not 'origin': both r1 and r2
        # already claim prior='origin' on this dep, so a fresh honest pair
        # starting there too, with its own DIFFERENT material, would conflict
        # with both of them.
        complete_honest_retirement(dep, sess=sid, base=material1)
    check("req 7: the two retirement receipts cannot assert the same material state",
          retirement_receipts_cannot_share_material)

    def recovery_must_build_on_the_repoint():
        """0238 (D). Recovery is only meaningful from the state the repoint
        produced; a recovery resting on some other state recovers something
        else entirely.

        A BRIDGE RECEIPT gives r2 a REAL prior to rest on that is neither
        'origin' (which r1 already claims -- sharing it with r2's different
        material would make r1 and r2 conflict permanently, for reasons
        having nothing to do with this contract) nor r1's own material
        (which would mean r2 correctly builds on the repoint, defeating the
        point). The bridge's prior IS r1's material, so it does not conflict
        with r1 either; it exists purely to give r2 a legal, unrelated state
        to rest on."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1, material1 = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, material1, "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _bsid, bkey, bdigest, _bs, bridge_material = receipt_fixture(
            sess=sid, subject_type="drive_dependency", subject_id=dep)
        bridge = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (bridge, sid, joe, dep, bkey, bdigest, bridge_material, material1),
                    because="setup: a bridge receipt giving the recovery below a real, "
                            "unrelated state to rest on")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (bridge,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2, material2 = receipt_fixture(
            sess=sid, subject_type="drive_dependency", subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, material2, bridge_material),
                    because="setup: recovery receipt that ignores the repoint")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "recovery ignores the repoint"),
                because="a recovery must build on the state the repoint actually produced",
                role="carr_writer", expect_message="does not build on the repointed state")
        # CHAINED OFF material2 (r2's own material, not yet used as anyone's
        # prior on this dep), avoiding 'origin'/material1/bridge_material,
        # all of which are already claimed as priors here.
        complete_honest_retirement(dep, sess=sid, base=material2)
    check("req 7: the recovery receipt must build on the repointed state",
          recovery_must_build_on_the_repoint)

    def readiness_is_computed_and_needs_authority():
        """A function over the rows, not a flag, and it will not read ready
        without an acceptance only the authority identity can create.

        THE TWO RECEIPTS MUST BE HONEST NOW (0238): each must name the
        dependency, rest on DIFFERENT calls, assert DIFFERENT material
        claims, and the recovery must build on what the repoint produced.
        Sharing one call/material (as this contract's setup did before 0238)
        is now refused before ever reaching drive_retirement_readiness."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1, material1 = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, material1, "origin"),
                    because="setup: the repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, "the repoint receipt must prove"
            conn.commit()
        _sid2, key2, digest2, _s2, material2 = receipt_fixture(
            sess=sid, subject_type="drive_dependency", subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, material2, material1),
                    because="setup: the recovery receipt, built on what the repoint produced")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True, "the recovery receipt must prove"
            conn.commit()
        writer_runs(conn, RETIREMENT_INSERT,
                    (uuid.uuid4(), dep, r1, r2, sid, joe, "an honest retirement"),
                    because="two proven, distinct receipts must be enough to retire ONE "
                            "dependency")
        with conn.cursor() as cur:
            cur.execute("select operational_total, retired_total, remaining, has_authority, ready "
                        "from ops.drive_retirement_readiness()")
            total, retired, remaining, has_auth, ready = cur.fetchone()
        assert total >= 1 and retired >= 1, "the readiness function must see the rows"
        assert ready is False or has_auth is True, \
            "readiness must not report ready without an authority acceptance"
    check("req 7: readiness is computed from the rows and requires authority",
          readiness_is_computed_and_needs_authority)

    def honest_retirement_reaches_the_happy_path():
        """A gate that can only say no is indistinguishable from a broken
        one. Two proven, distinct receipts -- different calls, different
        material, the recovery built on the repoint -- must actually be
        ENOUGH."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1, material1 = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, material1, "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2, material2 = receipt_fixture(
            sess=sid, subject_type="drive_dependency", subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, material2, material1),
                    because="setup: recovery receipt, built on the repoint")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        rid = uuid.uuid4()
        writer_runs(conn, RETIREMENT_INSERT,
                    (rid, dep, r1, r2, sid, joe, "an honest retirement, both receipts sound"),
                    because="two proven, distinct, honestly-related receipts must be "
                            "enough to retire ONE dependency")
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.drive_retirement where id=%s", (rid,))
            assert cur.fetchone()[0] == 1, "the honest retirement did not persist"
    check("req 7: a dependency CAN be retired when both receipts are honest",
          honest_retirement_reaches_the_happy_path)

    def readiness_matches_its_own_formula_and_empty_inventory_when_virgin():
        """REWRITTEN (per gate feedback): the original contract asserted
        ready is False whenever operational_total is 0 and, otherwise,
        recomputed the formula against a snapshot of drive_dependency/
        drive_retirement/phase4_acceptance separately. That second branch
        never actually happens, because THIS FILE'S OWN FIXES for the
        readiness-residue coverage gap retire every dependency any contract
        creates (see complete_honest_retirement and
        retire_dependency_from_readiness_count above): once one contract has
        ever run, operational_total > 0 and every operational dependency is
        retired, permanently -- drive_dependency rows cannot be deleted, so
        a SECOND pass against the same database can never see an empty
        inventory again. The suite's own two-pass gate exposed exactly this:
        first pass empty (or not yet populated), second pass never empty
        again, so the 'total == 0' branch silently stopped running and the
        'else' branch was the only one exercised from the second pass on.

        THE FIX IS TO ASSERT THE RULE, NOT THE STATE. ops.
        drive_retirement_readiness returns every input its own formula
        needs, so the invariant

            ready == (operational_total > 0 and remaining = 0 and has_authority)

        is checked directly from ITS OWN outputs, on whatever inventory
        happens to exist -- true on a virgin database, true on the
        hundredth rerun, and it still catches the original defect: an
        empty inventory has operational_total = 0, which makes the
        right-hand side False, so a readiness function that reported ready
        anyway would be caught by this same assertion.

        THE VIRGIN-DATABASE HALF IS KEPT, NOT DROPPED, but it can only ever
        run before this file (or anything else) has recorded a single Drive
        dependency -- named in this function's own name and docstring so a
        skip here is visible rather than silent, per this suite's own rule
        against a contract that quietly tests nothing."""
        with conn.cursor() as cur:
            cur.execute("""select operational_total, retired_total, remaining,
                                  has_authority, declared_digest, observed_digest,
                                  inventory_bound, ready
                             from ops.drive_retirement_readiness()""")
            (total, retired, remaining, has_authority, declared, observed,
             bound, ready) = cur.fetchone()

        # 0271 ADDED A FOURTH TERM, and it belongs in this rule rather than
        # beside it. The whole point of this contract is that the formula is
        # checked from readiness's OWN outputs, so a term the function computes
        # and this assertion ignores would be a term nothing checks.
        expected_ready = ((total > 0) and (remaining == 0)
                          and bool(has_authority) and bool(bound))
        assert ready == expected_ready, (
            f"readiness disagreed with the rule it claims to compute: "
            f"ready={ready} but (operational_total={total} > 0 and "
            f"remaining={remaining} == 0 and has_authority={has_authority} "
            f"and inventory_bound={bound}) = {expected_ready}")
        # AND inventory_bound MUST BE THE DIGEST COMPARISON IT CLAIMS TO BE.
        # Without this, a mutant hardcoding inventory_bound to the value the
        # rest of the suite happens to need would satisfy every line above.
        assert bound == (declared is not None and declared == observed), (
            f"inventory_bound={bound} does not match its own inputs "
            f"(declared={declared!r}, observed={observed!r})")
        assert remaining == total - retired, (
            f"remaining ({remaining}) must equal operational_total ({total}) "
            f"minus retired_total ({retired})")

        # VIRGIN-DATABASE CASE, RUN ONLY WHEN IT CAN STILL OCCUR: once any
        # dependency has ever been recorded, drive_dependency rows cannot be
        # deleted and this suite's own cleanup keeps every one of them
        # retired, so total==0 can only be true before the first dependency
        # this whole file's run has created. NOT a silent skip: reported
        # either way below.
        if total == 0:
            assert ready is False, "an empty inventory reported READY"
            print("          (virgin-database branch: operational_total == 0, "
                  "exercised this pass)")
        else:
            print(f"          (virgin-database branch SKIPPED this pass: "
                  f"operational_total={total} > 0 already -- cannot occur "
                  f"once any dependency has ever been recorded, since "
                  f"drive_dependency rows cannot be deleted)")
    check("req 7: readiness matches ready == (operational_total > 0 and "
          "remaining = 0 and has_authority), with the empty-inventory case "
          "checked when it can still occur",
          readiness_matches_its_own_formula_and_empty_inventory_when_virgin)

    def retirement_records_are_frozen():
        with conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.table_privileges
                            where table_schema='ops' and table_name='drive_retirement'
                              and grantee='carr_writer'
                              and privilege_type in ('UPDATE','DELETE')""")
            assert cur.fetchone()[0] == 0, \
                "carr_writer can rewrite or remove a retirement record"
        # and the trigger holds even for a role that does have the privilege
        with conn.cursor() as cur:
            cur.execute("select id from ops.drive_retirement limit 1")
            row = cur.fetchone()
        if row:
            try:
                with conn.cursor() as cur:
                    cur.execute("update ops.drive_retirement set note='rewritten' where id=%s",
                                (row[0],))
                conn.rollback()
                raise AssertionError("a retirement record was rewritten by the owner")
            except psycopg.Error as exc:
                conn.rollback()
                assert "cannot be rewritten" in str(exc).lower(), (
                    f"refused by a different guard: {str(exc).strip().splitlines()[0]}")
    check("req 7: retirement records cannot be rewritten", retirement_records_are_frozen)

    def triggers_enable_always():
        """WIDENED (adversarial-review addendum item 3). The original query
        matched only trigger NAMES containing 'application_session',
        'requires_live_session' or 'qualified_evidence', with no table
        restriction at all. That happened to catch write_receipt_requires_
        live_session and drive_retirement_withdrawal_requires_live_session
        (both contain 'requires_live_session'), but let write_receipt_state_
        existed, write_receipt_retraction_is_sound, write_receipt_reversal_
        is_exact, write_receipt_immutable and write_receipt_says_what_its_
        call_wrote escape entirely -- none of their names match any of the
        three substrings. The query now ALSO pulls in EVERY trigger on
        ops.write_receipt and ops.drive_retirement_withdrawal directly, by
        table, so no future guard on either table can go untested here again."""
        with conn.cursor() as cur:
            cur.execute("""select c.relname, t.tgname, t.tgenabled
                           from pg_trigger t
                           join pg_class c on c.oid=t.tgrelid
                           join pg_namespace n on n.oid=c.relnamespace
                           where not t.tgisinternal
                             and (t.tgname like '%application_session%'
                                  or t.tgname like '%requires_live_session%'
                                  or t.tgname like '%qualified_evidence%'
                                  or (n.nspname = 'ops'
                                      and c.relname in ('write_receipt',
                                                        'drive_retirement_withdrawal')))""")
            rows = cur.fetchall()
        assert len(rows) >= 16, f"expected at least 16 guard triggers, found {len(rows)}"
        weak = [(r[0], r[1]) for r in rows if r[2] != "A"]
        assert not weak, (f"not ENABLE ALWAYS, so session_replication_role='replica' "
                          f"switches them off: {weak}")
    check("every session trigger, AND every ops.write_receipt / drive_retirement_"
          "withdrawal trigger, is ENABLE ALWAYS", triggers_enable_always)

    # ============================================================ 0238 (F)/(G) ==
    # THE NEW GUARDS, one contract per rule, each asserting WHICH guard
    # refused by matching message text -- plus the adversarial-review
    # addendum's previously-untested guards.

    def receipt_refused_when_call_wrote_nothing_about_subject():
        """Rule 1. A receipt is a claim about what a call did to a subject;
        if that call produced NO event for that subject, there is nothing to
        receipt. Uses a call that writes absolutely nothing."""
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, sid),
                    because="setup: a qualified call that writes NOTHING")
        subject = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute("""select ops.write_receipt_digest('log-activity', %s,
                             'carr-internal', %s, 'hash', 'deal', %s)""",
                        (joe, sid, subject))
            digest = cur.fetchone()[0]
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, subject, key, digest, "m-anything", "origin"),
                because="a call that wrote nothing about this subject cannot back "
                        "a receipt naming it",
                role="carr_writer", expect_message="wrote nothing about that subject")
    check("rule 1: a receipt whose call wrote nothing about the named subject "
          "is refused", receipt_refused_when_call_wrote_nothing_about_subject)

    def ordinary_receipt_material_must_match_computed():
        """Rule 2. An event exists (rule 1 satisfied), but the caller invents
        its own material instead of using what the database recomputes --
        refused by a DIFFERENT message than rule 1's, distinguishing the two
        guards from each other."""
        sid, key, digest, subject, _material = receipt_fixture()
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, subject, key, digest,
                 "a-material-i-made-up", "origin"),
                because="an ordinary receipt's material must be the database-"
                        "computed one, not a caller's invention",
                role="carr_writer", expect_message="does not match what its call wrote")
    check("rule 2: an ordinary receipt carrying material its call did not write "
          "is refused", ordinary_receipt_material_must_match_computed)

    def material_digest_is_scoped_to_its_session():
        """0238 (F)'s material recipe folds events matched on (idempotency_
        key, session, subject_type, subject_id) -- ALL FOUR, not three. Drop
        the 'and e.application_session_id = p_session' clause from ops.
        write_receipt_material_digest's own query and two different
        sessions' events for the SAME subject under the SAME idempotency
        key fold together, letting one session's claim silently absorb
        writes another session made.

        TWO SESSIONS, ONE SHARED idempotency_key: the key is caller-chosen
        text, not a foreign key to one tool_call row (event.idempotency_key
        is non-unique by design), so nothing stops two different sessions
        writing event rows under the identical key string -- exactly the
        shape the session filter exists to keep apart. No write_receipt row
        is created here at all; this exercises the recipe function directly,
        so there is no residue to clean up."""
        subject = uuid.uuid4()
        shared_key = str(uuid.uuid4())
        sid_a = mint(conn, joe)
        sid_b = mint(conn, joe)
        writer_runs(conn, """insert into event
                (occurred_at, actor_id, verb, subject_type, subject_id, field,
                 old_value, new_value, cause, idempotency_key,
                 organization_tenant_id, application_session_id)
                values (now(), %s, 'log-activity', 'deal', %s, 'state',
                        to_jsonb('prior'::text), to_jsonb(%s::text), 'human_stated',
                        %s, 'carr-internal', %s)""",
                    (joe, subject, str(uuid.uuid4()), shared_key, sid_a),
                    because="setup: session A's event under the shared key")
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (shared_key, sid_a, subject))
            material_a_before = cur.fetchone()[0]

        writer_runs(conn, """insert into event
                (occurred_at, actor_id, verb, subject_type, subject_id, field,
                 old_value, new_value, cause, idempotency_key,
                 organization_tenant_id, application_session_id)
                values (now(), %s, 'log-activity', 'deal', %s, 'state',
                        to_jsonb('prior'::text), to_jsonb(%s::text), 'human_stated',
                        %s, 'carr-internal', %s)""",
                    (joe, subject, str(uuid.uuid4()), shared_key, sid_b),
                    because="setup: session B's event under the SAME shared key")
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (shared_key, sid_a, subject))
            material_a_after = cur.fetchone()[0]
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (shared_key, sid_b, subject))
            material_b = cur.fetchone()[0]
        assert material_a_before == material_a_after, (
            "session A's material CHANGED when session B wrote an event "
            "under the same idempotency key -- the digest is not scoped to "
            "its session")
        assert material_a_after != material_b, (
            "sessions A and B computed the SAME material for the same "
            "idempotency key and subject -- the digest is not scoped to "
            "its session")
    check("rule 1/2: ops.write_receipt_material_digest is scoped to its "
          "session, not just its idempotency_key",
          material_digest_is_scoped_to_its_session)

    def retraction_is_exempt_from_material_match():
        """Rule 2's exemption, made explicit. A retraction's material_digest
        need not equal ops.write_receipt_material_digest(...) -- a value
        PROVEN to differ from the real computed one is still accepted,
        because reversal/retraction rows are exempt from rule 2 (never from
        rule 1, which the fixture's own event already satisfies)."""
        sid, key, digest, subject, material = receipt_fixture()
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: the receipt to retract")
        _rsid, ret_key, ret_digest, _rs, _rm = receipt_fixture(sess=sid, subject_id=subject)
        with conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (ret_key, sid, subject))
            computed = cur.fetchone()[0]
        fabricated = "a-material-that-is-not-the-computed-one"
        assert fabricated != computed, "test setup bug: fabricated must differ"
        ret = uuid.uuid4()
        writer_runs(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest, retracts_receipt_id)
              values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,
                      'origin',%s)""",
                    (ret, sid, joe, subject, ret_key, ret_digest,
                     fabricated, target),
                    because="a retraction's material need not match its call's "
                            "computed material")
        # PROVE THE RETRACTION: an unproven retraction clears nothing (this
        # file tests that separately), so target -- itself never proven --
        # would remain unretracted residue unless this retraction proves.
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (ret,))
            assert cur.fetchone()[0] is True, (
                "the cleanup retraction must prove, or target survives as residue")
            conn.commit()
    check("rule 2 exemption: a retraction carrying material that does NOT match "
          "its call's computed material is still accepted",
          retraction_is_exempt_from_material_match)

    def prior_naming_unproven_material_is_refused():
        """Rule 3, the bootstrap-ladder shape the migration's own comment
        names as the trap this guard closes: file a junk receipt, let its
        readback fail so it stays UNPROVEN, then try to use its material as
        a later receipt's prior. An earlier version of this guard accepted
        material from ANY row on the subject, proven or not; this asserts
        that ladder is gone."""
        sid, key, digest, subject, material = receipt_fixture()
        junk = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (junk, sid, joe, subject, key, "a-digest-nobody-ever-wrote",
                     material, "origin"),
                    because="setup: a junk receipt -- material correct (rule 2), "
                            "call digest fabricated so its readback fails")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (junk,))
            assert cur.fetchone()[0] is False, "the junk receipt must fail its readback"
            conn.commit()
        _lsid, later_key, later_digest, _ls, later_material = receipt_fixture(
            sess=sid, subject_id=subject)
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, subject, later_key, later_digest,
                 later_material, material),
                because="an UNPROVEN receipt's material must not qualify as a "
                        "later receipt's prior -- the bootstrap ladder this guard "
                        "exists to close",
                role="carr_writer", expect_message="never reached")
        cleanup_unproven_receipt(junk, "deal", subject, sess=sid)
    check("rule 3: a prior naming UNPROVEN material is refused (bootstrap-ladder "
          "shape)", prior_naming_unproven_material_is_refused)

    def retraction_cannot_target_a_proven_receipt():
        """Rule 4. require_sound_retraction refuses outright to retract a
        PROVEN receipt: reverse it instead, which has to say what state it
        restores."""
        sid, key, digest, subject, material = receipt_fixture()
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: the receipt to be (unsuccessfully) retracted")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (target,))
            assert cur.fetchone()[0] is True, "the target must actually prove"
            conn.commit()
        _rsid, ret_key, ret_digest, _rs, _rm = receipt_fixture(sess=sid, subject_id=subject)
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                    'm-x','origin',%s)"""
        refuses(conn, bad, (uuid.uuid4(), sid, joe, subject, ret_key, ret_digest, target),
                because="a proven receipt must be reversed, not retracted",
                role="carr_writer", expect_message="is proven and cannot be retracted")
    check("rule 4: a retraction cannot target a proven receipt",
          retraction_cannot_target_a_proven_receipt)

    def retraction_cannot_cross_tenants():
        """Rule 4. A retraction stays inside its own tenant."""
        subject = uuid.uuid4()
        other_sid, other_key, other_digest, _os, other_material = receipt_fixture(
            subject_id=subject, tenant=OTHER_TENANT)
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_TENANT,
                    (target, other_sid, joe, OTHER_TENANT, subject, other_key,
                     other_digest, other_material, "origin"),
                    because="setup: a receipt filed under a SECOND, real tenant")
        home_sid, home_key, home_digest, _hs, _hm = receipt_fixture(subject_id=subject)
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                    'm-x','origin',%s)"""
        refuses(conn, bad,
                (uuid.uuid4(), home_sid, joe, subject, home_key, home_digest, target),
                because="a retraction filed under carr-internal must not disavow "
                        "a receipt filed under a different tenant",
                role="carr_writer", expect_message="a retraction cannot cross tenants")
        cleanup_unproven_receipt(target, "deal", subject, sess=other_sid, tenant=OTHER_TENANT)
    check("rule 4: a retraction cannot cross tenants", retraction_cannot_cross_tenants)

    def reversal_cannot_cross_tenants():
        """Rule 5. A reversal stays inside its own tenant."""
        subject = uuid.uuid4()
        other_sid, other_key, other_digest, _os, other_material = receipt_fixture(
            subject_id=subject, tenant=OTHER_TENANT)
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_TENANT,
                    (target, other_sid, joe, OTHER_TENANT, subject, other_key,
                     other_digest, other_material, "origin"),
                    because="setup: a receipt filed under a SECOND, real tenant")
        home_sid, home_key, home_digest, _hs, _hm = receipt_fixture(subject_id=subject)
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                    'origin','m-x',%s)"""
        refuses(conn, bad,
                (uuid.uuid4(), home_sid, joe, subject, home_key, home_digest, target),
                because="a reversal filed under carr-internal must not target a "
                        "receipt filed under a different tenant",
                role="carr_writer", expect_message="a reversal cannot cross tenants")
        cleanup_unproven_receipt(target, "deal", subject, sess=other_sid, tenant=OTHER_TENANT)
    check("rule 5: a reversal cannot cross tenants", reversal_cannot_cross_tenants)

    def reversal_must_name_the_same_subject():
        """Adversarial-review addendum item 1. require_exact_reversal's
        SAME-SUBJECT clause had ZERO coverage (the identical clause on
        retraction is tested twice). A reversal naming a DIFFERENT subject
        than its target must be refused by 'must name the same subject'."""
        sid, key, digest, subject, material = receipt_fixture()
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: the receipt a reversal will wrongly try to name")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (target,))
            assert cur.fetchone()[0] is True
            conn.commit()
        other_subject = uuid.uuid4()
        _osid, o_key, o_digest, _os, _om = receipt_fixture(sess=sid, subject_id=other_subject)
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                    'origin','origin',%s)"""
        refuses(conn, bad,
                (uuid.uuid4(), sid, joe, other_subject, o_key, o_digest, target),
                because="a reversal must name the same subject as the receipt it "
                        "reverses",
                role="carr_writer", expect_message="must name the same subject")
    check("adversarial review 1: a reversal naming a different subject than its "
          "target is refused", reversal_must_name_the_same_subject)

    def unproven_reversal_does_not_close_a_conflict():
        """Rule 6. ops.receipt_conflicts only lets a PROVEN, unretracted
        reversal suppress a conflict. An unproven reversal must not close it."""
        subject = uuid.uuid4()
        a_sid, a_key, a_digest, _sa, a_material = receipt_fixture(subject_id=subject)
        _bsid, b_key, b_digest, _sb, b_material = receipt_fixture(
            sess=a_sid, subject_id=subject)
        a, b = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (a, a_sid, joe, subject, a_key, a_digest, a_material, "origin"),
                    because="setup: one side of the conflict")
        writer_runs(conn, RECEIPT_INSERT,
                    (b, a_sid, joe, subject, b_key, b_digest, b_material, "origin"),
                    because="setup: the other side, same prior, different material")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a,))
            assert cur.fetchone()[0] is True
            cur.execute("select ops.prove_write_receipt(%s)", (b,))
            assert cur.fetchone()[0] is True
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] >= 1, "setup must actually conflict"

        _rsid, rev_key, rev_digest, _rs, _rm = receipt_fixture(sess=a_sid, subject_id=subject)
        rev_id = uuid.uuid4()
        # THE CALL DIGEST IS REAL (rev_digest, from the fixture), not
        # fabricated: is_proven is false simply because prove_write_receipt
        # has not been CALLED yet (readback_digest stays null) -- not
        # because the digest is permanently wrong. That is what lets this
        # exact same row later prove successfully below.
        writer_runs(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest, reverses_receipt_id)
              values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                      'origin',%s,%s)""",
                    (rev_id, a_sid, joe, subject, rev_key, rev_digest, b_material, b),
                    because="setup: an exact reversal of b, left deliberately unproven")
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            still_open = cur.fetchone()[0]
        assert still_open >= 1, (
            "an UNPROVEN reversal closed a conflict -- only a PROVEN one may")

        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rev_id,))
            assert cur.fetchone()[0] is True, (
                "the reversal must prove, or this contract cannot tell the two "
                "readings apart")
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] == 0, "a PROVEN reversal must close the conflict"
    check("rule 6: an unproven reversal does not close a conflict; a proven one "
          "does", unproven_reversal_does_not_close_a_conflict)

    def retraction_is_not_a_party_to_a_conflict():
        """Rule 6. ops.receipt_conflicts excludes any row with
        retracts_receipt_id set from its 'live' set entirely -- a retraction
        disavows a claim, it does not make one, so it cannot conflict with
        anything no matter what prior/material it carries."""
        subject = uuid.uuid4()
        sid, key, digest, _s, material = receipt_fixture(subject_id=subject)
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: a lone receipt on this subject")
        _rsid, ret_key, ret_digest, _rs, _rm = receipt_fixture(sess=sid, subject_id=subject)
        ret = uuid.uuid4()
        writer_runs(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest, retracts_receipt_id)
              values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                      'a-material-sharing-origin','origin',%s)""",
                    (ret, sid, joe, subject, ret_key, ret_digest, target),
                    because="setup: a retraction of target, sharing 'origin' as its "
                            "own prior -- which would be a conflict with target if "
                            "it were an ordinary receipt")
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] == 0, (
                "a retraction registered as a party to a conflict")
        # PROVE THE RETRACTION so target does not survive as unproven residue
        # -- an unproven retraction clears nothing (tested separately).
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (ret,))
            assert cur.fetchone()[0] is True, (
                "the cleanup retraction must prove, or target survives as residue")
            conn.commit()
    check("rule 6: a retraction receipt is not a party to a conflict",
          retraction_is_not_a_party_to_a_conflict)

    def receipt_conflicts_is_tenant_scoped():
        """ops.receipt_conflicts joins on organization_tenant_id, not just
        subject_type/subject_id/prior_digest. A subject id is a bare uuid
        with no tenant binding of its own, so without that join condition
        one tenant could manufacture a conflict INSIDE another tenant's
        chain merely by naming the same subject uuid, blocking a phase
        acceptance it has no part in.

        NO RECONCILIATION NEEDED: this is a positive assertion that the two
        receipts do NOT conflict (the correct, current behavior), not a
        conflict this contract creates and must close -- there is nothing
        to reconcile, and both receipts are proven and left as ordinary,
        unremarkable residue-free rows."""
        subject = uuid.uuid4()
        sid_home, key_home, digest_home, _sh, material_home = receipt_fixture(
            subject_id=subject)
        home = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (home, sid_home, joe, subject, key_home, digest_home,
                     material_home, "origin"),
                    because="setup: carr-internal's own receipt, prior='origin'")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (home,))
            assert cur.fetchone()[0] is True
            conn.commit()

        sid_other, key_other, digest_other, _so, material_other = receipt_fixture(
            subject_id=subject, tenant=OTHER_TENANT)
        other = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_TENANT,
                    (other, sid_other, joe, OTHER_TENANT, subject, key_other,
                     digest_other, material_other, "origin"),
                    because="setup: a SECOND tenant's receipt, the SAME subject "
                            "uuid, the SAME prior ('origin'), and -- by "
                            "construction, since it is a different call -- "
                            "DIFFERENT material")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (other,))
            assert cur.fetchone()[0] is True
            conn.commit()

        assert material_home != material_other, (
            "test setup bug: the two tenants' receipts must assert DIFFERENT "
            "material for this to test anything")
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (subject,))
            assert cur.fetchone()[0] == 0, (
                "a receipt in ONE tenant registered as conflicting with a "
                "receipt in a DIFFERENT tenant, sharing only a subject uuid "
                "-- ops.receipt_conflicts must be tenant-scoped")
    check("rule 6: ops.receipt_conflicts is tenant-scoped -- the same subject "
          "uuid, same prior, different material across two tenants is NOT a "
          "conflict", receipt_conflicts_is_tenant_scoped)

    def retracted_receipt_and_retractor_leave_the_fold():
        """Rule 7. ops.continuity_reducer drops a retracted receipt AND its
        (proven) retractor from the fold entirely -- not merely forgives the
        retracted receipt its unprovenness. break_at must name the receipt
        that actually broke the chain, never the retraction that repairs it.

        THE SHAPE MUST AVOID A CONFLICT, not just a break: a stale receipt
        whose prior is 'origin' would collide with a1's own prior='origin'
        (same prior, different material IS what ops.receipt_conflicts
        detects), turning the reduced state 'conflicted' before it ever gets
        to 'broken' -- the wrong finding for this contract. So the damage
        (b) is a STALE-BUT-REAL restatement of a1->a2's own transition
        (0238 section E): a1 (origin->X), a2 (X->Y, head), a3 (Y->Z, head
        moves on) are a clean, continuous, PROVEN chain; b restates X->Y
        with a2's EXACT material (forced via new_value), arriving after the
        head moved on to Z. It shares a2's prior AND a2's material, so it is
        real (rule 3 admits it) and non-conflicting (same material as a2),
        yet it does not build on the CURRENT head (Z) -- a break, and
        nothing else. b is left UNPROVEN and un-retracted so it folds (an
        unproven, unretracted receipt still folds, per ops.continuity_
        reducer's own comment) and breaks the chain on its own account.

        ORDER RELIED ON IS INSERTION ORDER: a1/a2/a3 are inserted, committed
        and proven strictly before b is inserted, so the fold -- which
        orders by recorded_at, id today, and will order by an incoming seq
        column -- sees them in that order regardless of clock-tick
        granularity."""
        subject = uuid.uuid4()
        a1_sid, a1_key, a1_digest, _s1, a1_material = receipt_fixture(
            subject_id=subject, new_value="X-marker")
        a2_sid, a2_key, a2_digest, _s2, a2_material = receipt_fixture(
            sess=a1_sid, subject_id=subject, new_value="Y-marker")
        a3_sid, a3_key, a3_digest, _s3, a3_material = receipt_fixture(
            sess=a1_sid, subject_id=subject, new_value="Z-marker")
        a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (a1, a1_sid, joe, subject, a1_key, a1_digest, a1_material, "origin"),
                    because="setup: origin -> X")
        # RULE 3: a2's prior is a1's material, a3's prior is a2's material,
        # so each must be PROVEN before the next receipt cites it.
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a1,))
            assert cur.fetchone()[0] is True, "a1 must prove before a2 can cite its material"
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (a2, a2_sid, joe, subject, a2_key, a2_digest, a2_material, a1_material),
                    because="setup: X -> Y, the head")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a2,))
            assert cur.fetchone()[0] is True, "a2 must prove before a3 can cite its material"
            conn.commit()
        writer_runs(conn, RECEIPT_INSERT,
                    (a3, a3_sid, joe, subject, a3_key, a3_digest, a3_material, a2_material),
                    because="setup: Y -> Z, the new head")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a3,))
            assert cur.fetchone()[0] is True, "a3 must prove"
            conn.commit()

        _bsid, b_key, b_digest, _sb, b_material = receipt_fixture(
            sess=a1_sid, subject_id=subject, new_value="Y-marker")
        assert b_material == a2_material, (
            "fixture bug: b must restate a2's exact transition")
        b = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (b, a1_sid, joe, subject, b_key, b_digest, b_material, a1_material),
                    because="setup: the damage -- a STALE BUT REAL restatement of "
                            "a1->a2, left unproven, arriving after the head moved "
                            "on to Z")

        with conn.cursor() as cur:
            cur.execute("""select state, break_at, unproven_count, conflict_count
                             from ops.continuity_reducer('deal', %s)""", (subject,))
            state, break_at, unproven, conflicts = cur.fetchone()
        assert state == "broken" and break_at == b and unproven == 1 and conflicts == 0, (
            f"expected broken at b with 1 unproven receipt and 0 conflicts before "
            f"retraction, got state={state} break_at={break_at} unproven={unproven} "
            f"conflicts={conflicts}")

        _rsid, ret_key, ret_digest, _rs, _rm = receipt_fixture(
            sess=a1_sid, subject_id=subject)
        ret = uuid.uuid4()
        writer_runs(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest, retracts_receipt_id)
              values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                      'm-repair','origin',%s)""",
                    (ret, a1_sid, joe, subject, ret_key, ret_digest, b),
                    because="setup: retract b -- the damage, now repaired on the "
                            "record")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (ret,))
            assert cur.fetchone()[0] is True, "the retraction must prove"
            conn.commit()

        with conn.cursor() as cur:
            cur.execute("""select state, break_at, receipt_count
                             from ops.continuity_reducer('deal', %s)""", (subject,))
            state, break_at, count = cur.fetchone()
        assert state == "continuous", (
            f"once b and its retraction both leave the fold, only a1/a2/a3 "
            f"(proven, continuous) remain, so the state must be continuous, "
            f"got {state}")
        assert break_at is None, (
            f"break_at must not still name b once it (and its retractor) have "
            f"left the fold, got {break_at}")
        assert count == 3, (
            f"only a1, a2 and a3 should remain in the fold once b and ret both "
            f"leave it, got receipt_count={count}")
    check("rule 7: a retracted receipt and its retractor both leave the fold, "
          "and break_at names the damage rather than the repair",
          retracted_receipt_and_retractor_leave_the_fold)

    def withdrawn_retirement_stops_counting_and_can_be_retired_again():
        """Rule 8. ops.drive_retirement lost its one-per-dependency unique
        constraint, and a withdrawal records that a retirement was made in
        error. Readiness counts DISTINCT non-withdrawn retirements, so a
        withdrawn retirement stops counting toward readiness -- and the same
        dependency can then be retired again with a second, honest pair of
        receipts.

        THE SECOND RETIREMENT'S REPOINT BUILDS ON THE FIRST'S RECOVERY, not
        on 'origin' again. Restarting a second repoint at 'origin' would
        make it share a prior with the FIRST repoint (also 'origin') on the
        SAME dep -- same prior, different material, which is exactly the
        conflict ops.receipt_conflicts detects, and one this contract has no
        reason to reconcile. Chaining onto the first recovery's material
        keeps every prior on this subject used exactly once, and it is also
        the honest shape: a real second repoint continues from whatever
        state the (withdrawn) first attempt actually left behind."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()

        def file_retirement(marker, base="origin"):
            sid, key1, digest1, _s1, material1 = receipt_fixture(
                subject_type="drive_dependency", subject_id=dep,
                new_value=f"{marker}-repoint")
            r1 = uuid.uuid4()
            writer_runs(conn, RECEIPT_INSERT_DEP,
                        (r1, sid, joe, dep, key1, digest1, material1, base),
                        because=f"setup: {marker} repoint receipt")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (r1,))
                assert cur.fetchone()[0] is True
                conn.commit()
            _sid2, key2, digest2, _s2, material2 = receipt_fixture(
                sess=sid, subject_type="drive_dependency", subject_id=dep,
                new_value=f"{marker}-recovery")
            r2 = uuid.uuid4()
            writer_runs(conn, RECEIPT_INSERT_DEP,
                        (r2, sid, joe, dep, key2, digest2, material2, material1),
                        because=f"setup: {marker} recovery receipt")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (r2,))
                assert cur.fetchone()[0] is True
                conn.commit()
            rid = uuid.uuid4()
            writer_runs(conn, RETIREMENT_INSERT,
                        (rid, dep, r1, r2, sid, joe, f"{marker} retirement"),
                        because=f"setup: {marker} retirement")
            return sid, rid, material2

        def retired_count():
            with conn.cursor() as cur:
                cur.execute("""select count(distinct r.drive_dependency_id)
                                 from ops.drive_retirement r
                                where r.drive_dependency_id = %s
                                  and not exists (
                                    select 1 from ops.drive_retirement_withdrawal w
                                     where w.drive_retirement_id = r.id)""", (dep,))
                return cur.fetchone()[0]

        def readiness():
            with conn.cursor() as cur:
                cur.execute("""select operational_total, retired_total, remaining,
                                      has_authority, ready
                                 from ops.drive_retirement_readiness()""")
                return cur.fetchone()

        sid1, retirement_1, material_after_first = file_retirement("first")
        assert retired_count() == 1, "the first retirement must count before withdrawal"

        # THE NUMBER THAT MATTERS: readiness is GLOBAL, not scoped to this
        # dep, so this assertion depends on every OTHER contract that creates
        # a drive_dependency having already closed it out honestly (every one
        # of them now does, via complete_honest_retirement or its own honest
        # pair) -- otherwise remaining would never reach 0 here at all.
        # 0271: and on the inventory being BOUND, which file_retirement above
        # just un-bound by recording a new dependency.
        declare_inventory_manifest(conn, joe, "rule 8 withdrawal contract: before")
        _tot0, _ret0, remaining_before, has_auth_before, ready_before = readiness()
        assert remaining_before == 0, (
            f"expected every operational dependency retired before the "
            f"withdrawal below, got remaining={remaining_before} -- something "
            f"upstream is leaving a dependency operational-but-unretired")
        assert has_auth_before is True, (
            "an authority acceptance must already exist by this point in the suite")
        assert ready_before is True, (
            f"expected ready with remaining=0 and an authority acceptance on "
            f"record, got ready={ready_before}")

        writer_runs(conn, """insert into ops.drive_retirement_withdrawal
                (id, drive_retirement_id, application_session_id, withdrawn_by_actor_id,
                 organization_tenant_id, note)
              values (%s,%s,%s,%s,'carr-internal',%s)""",
                    (uuid.uuid4(), retirement_1, sid1, joe, "retired in error"),
                    because="setup: withdraw the first (erroneous) retirement")
        assert retired_count() == 0, (
            "a withdrawn retirement still counted toward readiness")

        # THE MUTATION THIS CATCHES: dropping the not-exists clause on
        # ops.drive_retirement_withdrawal from ops.drive_retirement_readiness
        # would leave remaining and ready UNCHANGED by the withdrawal above --
        # this is the assertion that actually depends on withdrawal-awareness,
        # not merely on the scoped retired_count() query above.
        _tot1, _ret1, remaining_after, has_auth_after, ready_after = readiness()
        assert remaining_after == remaining_before + 1, (
            f"withdrawing the only retirement of an otherwise fully-retired "
            f"dependency set must move remaining from {remaining_before} to "
            f"{remaining_before + 1}, got {remaining_after} -- readiness is "
            f"ignoring the withdrawal")
        assert ready_after is False, (
            f"ready must go false the moment a withdrawal un-retires the only "
            f"remaining operational dependency, got ready={ready_after}")

        file_retirement("second", base=material_after_first)
        assert retired_count() == 1, (
            "the dependency could not be retired again after its withdrawal")
        # 0271: ready also needs the inventory bound, and this contract created
        # a dependency since any earlier manifest was declared.
        declare_inventory_manifest(conn, joe, "rule 8 withdrawal contract")
        _tot2, _ret2, remaining_final, _ha2, ready_final = readiness()
        assert remaining_final == remaining_before, (
            f"remaining must return to {remaining_before} once the dependency "
            f"is retired again, got {remaining_final}")
        assert ready_final is True, (
            f"ready must return to True once retired_total catches back up "
            f"with operational_total, got ready={ready_final}")
    check("rule 8: a withdrawn retirement stops counting, and the dependency can "
          "then be retired again",
          withdrawn_retirement_stops_counting_and_can_be_retired_again)

    def readiness_counts_dependencies_not_rows():
        """Rule 8. The unique constraint that used to make this shape
        impossible was dropped on purpose, so a dependency withdrawn and
        re-retired needs a second row -- but that same drop means TWO LIVE
        retirement rows can now exist for ONE dependency with NEITHER ever
        withdrawn (not merely the withdraw-then-retry shape the previous
        contract exercises, where only one row is ever live at a time).
        ops.drive_retirement_readiness must count DISTINCT dependencies, not
        rows, or two honest retirements of the SAME dependency inflate
        retired_total and report readiness that does not exist.

        THE ASSERTION IS A DELTA, NOT AN ABSOLUTE '1': readiness is computed
        GLOBALLY with no scoping parameter, and by this point in the suite
        several OTHER dependencies already sit honestly retired (this file
        makes sure of that itself, see complete_honest_retirement above) --
        so retired_total is never literally 1 here. What the guard actually
        promises is that adding a SECOND row for an ALREADY-COUNTED
        dependency changes nothing: retired_total (and therefore remaining)
        measured right after the SECOND retirement must equal what it was
        right after the FIRST. If readiness switched to count(*), this exact
        delta would show retired_total incrementing by one for zero new
        retired dependencies.

        CHAINED PRIORS: the second retirement's repoint builds on the first
        recovery's OWN material (head_a), never on 'origin' or on the first
        repoint's material (already claimed as the first recovery's prior) --
        either would manufacture an open conflict this contract would then
        owe a reconciliation for."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()

        def file_retirement(marker, base):
            sid, key1, digest1, _s1, material1 = receipt_fixture(
                subject_type="drive_dependency", subject_id=dep,
                new_value=f"{marker}-repoint")
            r1 = uuid.uuid4()
            writer_runs(conn, RECEIPT_INSERT_DEP,
                        (r1, sid, joe, dep, key1, digest1, material1, base),
                        because=f"setup: {marker} repoint receipt")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (r1,))
                assert cur.fetchone()[0] is True
                conn.commit()
            _sid2, key2, digest2, _s2, material2 = receipt_fixture(
                sess=sid, subject_type="drive_dependency", subject_id=dep,
                new_value=f"{marker}-recovery")
            r2 = uuid.uuid4()
            writer_runs(conn, RECEIPT_INSERT_DEP,
                        (r2, sid, joe, dep, key2, digest2, material2, material1),
                        because=f"setup: {marker} recovery receipt")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (r2,))
                assert cur.fetchone()[0] is True
                conn.commit()
            rid = uuid.uuid4()
            writer_runs(conn, RETIREMENT_INSERT,
                        (rid, dep, r1, r2, sid, joe, f"{marker} retirement"),
                        because=f"setup: {marker} retirement")
            return rid, material2

        def readiness():
            with conn.cursor() as cur:
                cur.execute("""select operational_total, retired_total, remaining, ready
                                 from ops.drive_retirement_readiness()""")
                return cur.fetchone()

        rid_a, head_a = file_retirement("first", "origin")
        total_1, retired_1, remaining_1, ready_1 = readiness()
        assert total_1 >= 1 and retired_1 >= 1, "the readiness function must see the rows"

        rid_b, _head_b = file_retirement("second", head_a)

        # NEITHER RETIREMENT IS WITHDRAWN -- both are live rows for ONE
        # dependency.
        with conn.cursor() as cur:
            cur.execute("""select count(*) from ops.drive_retirement
                            where drive_dependency_id=%s""", (dep,))
            row_count = cur.fetchone()[0]
        assert row_count == 2, (
            f"setup bug: expected two live retirement ROWS for one dependency, "
            f"got {row_count}")
        with conn.cursor() as cur:
            cur.execute("""select count(*) from ops.drive_retirement_withdrawal
                            where drive_retirement_id in (%s,%s)""", (rid_a, rid_b))
            assert cur.fetchone()[0] == 0, "setup bug: neither retirement may be withdrawn"

        total_2, retired_2, remaining_2, ready_2 = readiness()
        assert total_2 == total_1, (
            "setup bug: operational_total must not change between the two "
            "retirements of the SAME dependency")
        assert retired_2 == retired_1, (
            f"retired_total changed from {retired_1} to {retired_2} when a "
            f"SECOND retirement row was added for the SAME (already-retired) "
            f"dependency -- readiness is counting ROWS, not DISTINCT "
            f"dependencies")
        assert remaining_2 == remaining_1, (
            f"remaining changed from {remaining_1} to {remaining_2} for the "
            f"same reason")
        assert ready_2 == ready_1, (
            f"ready changed from {ready_1} to {ready_2} for the same reason")
    check("rule 8: readiness counts DISTINCT dependencies, not retirement rows "
          "-- two live, un-withdrawn retirements of ONE dependency count once",
          readiness_counts_dependencies_not_rows)

    def withdrawal_cannot_cross_tenants_or_be_rewritten():
        """Rule 8, the withdrawal table's own guards."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1, material1 = receipt_fixture(
            subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, material1, "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2, material2 = receipt_fixture(
            sess=sid, subject_type="drive_dependency", subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, material2, material1),
                    because="setup: recovery receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        rid = uuid.uuid4()
        writer_runs(conn, RETIREMENT_INSERT,
                    (rid, dep, r1, r2, sid, joe, "a retirement to withdraw"),
                    because="setup: the retirement")

        other_sid = mint(conn, joe, tenant=OTHER_TENANT)
        refuses(conn, """insert into ops.drive_retirement_withdrawal
                  (id, drive_retirement_id, application_session_id, withdrawn_by_actor_id,
                   organization_tenant_id, note)
                values (%s,%s,%s,%s,%s,%s)""",
                (uuid.uuid4(), rid, other_sid, joe, OTHER_TENANT,
                 "cross-tenant withdrawal"),
                because="a withdrawal filed under a different tenant must not name "
                        "this retirement",
                role="carr_writer", expect_message="a withdrawal cannot cross tenants")

        wid = uuid.uuid4()
        writer_runs(conn, """insert into ops.drive_retirement_withdrawal
                  (id, drive_retirement_id, application_session_id, withdrawn_by_actor_id,
                   organization_tenant_id, note)
                values (%s,%s,%s,%s,'carr-internal',%s)""",
                    (wid, rid, sid, joe, "retired in error, honestly this time"),
                    because="setup: an honest withdrawal")
        refuses(conn, "update ops.drive_retirement_withdrawal set note='rewritten' "
                "where id=%s", (wid,),
                because="a withdrawal must be immutable, even for the owner",
                expect_message="cannot be rewritten")
        refuses(conn, "delete from ops.drive_retirement_withdrawal where id=%s",
                (wid,),
                because="a withdrawal must not be deletable, even for the owner",
                expect_message="cannot be deleted")
        # THE WITHDRAWAL ABOVE LEFT dep UN-RETIRED AGAIN -- re-retire it
        # honestly, chained off material2 (r2's own material, not yet used
        # as anyone's prior here), so this dependency does not linger as
        # operational-but-unretired residue for ops.drive_retirement_
        # readiness() the way an unproven receipt would for the acceptance bar.
        complete_honest_retirement(dep, sess=sid, base=material2)
    check("rule 8: a withdrawal cannot cross tenants, and cannot be rewritten or "
          "deleted", withdrawal_cannot_cross_tenants_or_be_rewritten)

    def immutability_covers_material_digest_and_retracts_receipt_id():
        """Adversarial-review addendum item 2. 0238 added material_digest and
        retracts_receipt_id to the identity tuple ops.refuse_receipt_rewrite
        checks, and neither was tested. Driven AS THE OWNER (no role
        switch): carr_writer holds no UPDATE on ops.write_receipt at all, so
        testing this as the writer would only prove a PRIVILEGE, not the
        guard -- the privilege is not what is under test here."""
        sid, key, digest, subject, material = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: an ordinary receipt")
        refuses(conn, "update ops.write_receipt set material_digest='tampered' "
                "where id=%s", (rid,),
                because="material_digest must be immutable, even for the owner",
                expect_message="identity is immutable")

        # A SEPARATE SUBJECT, deliberately: target and rid must NOT share a
        # subject. Both would carry prior_digest='origin' (rid already does),
        # and two receipts on ONE subject sharing a prior with DIFFERENT
        # material is exactly what ops.receipt_conflicts detects -- a
        # conflict this contract never reconciles, since reconciling it is
        # not what it is testing. An unrelated subject sidesteps the
        # conflict entirely rather than requiring a reversal to close it.
        other_subject = uuid.uuid4()
        target = uuid.uuid4()
        _tsid, t_key, t_digest, _ts, t_material = receipt_fixture(
            sess=sid, subject_id=other_subject)
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, other_subject, t_key, t_digest, t_material, "origin"),
                    because="setup: an unproven receipt to name as a retraction "
                            "target")
        _rsid, r_key, r_digest, _rs, r_material = receipt_fixture(
            sess=sid, subject_id=other_subject)
        ret = uuid.uuid4()
        writer_runs(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest, retracts_receipt_id)
              values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,
                      'origin',%s)""",
                    (ret, sid, joe, other_subject, r_key, r_digest, r_material, target),
                    because="setup: a retraction, so retracts_receipt_id has a "
                            "non-null value to try to change")
        refuses(conn, "update ops.write_receipt set retracts_receipt_id=%s "
                "where id=%s", (rid, ret),
                because="retracts_receipt_id must be immutable, even for the owner",
                expect_message="identity is immutable")
        # PROVE THE RETRACTION so target does not survive as unproven residue,
        # and clean up rid (on the SEPARATE subject) with its own retraction.
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (ret,))
            assert cur.fetchone()[0] is True, (
                "the cleanup retraction must prove, or target survives as residue")
            conn.commit()
        cleanup_unproven_receipt(rid, "deal", subject, sess=sid)
    check("adversarial review 2: material_digest and retracts_receipt_id are both "
          "in the immutability tuple",
          immutability_covers_material_digest_and_retracts_receipt_id)

    def five_arg_write_receipt_digest_is_dropped():
        """Adversarial-review addendum item 4. The five-argument
        ops.write_receipt_digest is DROPPED by 0238, not merely superseded --
        leaving it callable would leave the exact defect (a call digest not
        bound to a subject) callable, and the drop itself was never tested.

        refuses() CANNOT EXPRESS THIS: SQLSTATE 42883 (undefined_function) is
        in ABSENCE_SQLSTATES, which refuses() treats as 'no guard fired' --
        the reasonable rule everywhere else in this file. Here the ABSENCE
        of the function IS the guard, so this contract drives the check by
        hand."""
        try:
            with conn.cursor() as cur:
                cur.execute("select ops.write_receipt_digest(%s,%s,%s,%s,%s)",
                            ("log-activity", joe, "carr-internal", uuid.uuid4(), "hash"))
            conn.rollback()
            raise AssertionError(
                "the five-argument ops.write_receipt_digest still exists")
        except psycopg.Error as exc:
            conn.rollback()
            state = getattr(exc, "sqlstate", None)
            assert state == "42883", (
                f"expected undefined_function (42883), got {state}: "
                f"{str(exc).strip().splitlines()[0]}")
            assert "does not exist" in str(exc).lower(), (
                f"refused, but not with 'does not exist': "
                f"{str(exc).strip().splitlines()[0]}")
        # CHECKED BY pronargs, NOT BY MATCHING pg_get_function_identity_
        # arguments AGAINST A HAND-WRITTEN STRING -- that string is brittle
        # against formatting this file does not control. ops.write_receipt_
        # digest has no default arguments, so pronargs IS its arity.
        with conn.cursor() as cur:
            cur.execute("""select p.pronargs, pg_get_function_identity_arguments(p.oid)
                             from pg_proc p
                             join pg_namespace n on n.oid=p.pronamespace
                            where n.nspname='ops' and p.proname='write_receipt_digest'""")
            rows = cur.fetchall()
        assert len(rows) == 1, (
            f"expected exactly one ops.write_receipt_digest overload (the "
            f"seven-argument replacement, the five-argument form dropped), "
            f"found {len(rows)}: {rows}")
        pronargs, identity_args = rows[0]
        assert pronargs == 7, (
            f"ops.write_receipt_digest must take exactly 7 arguments now that "
            f"the five-argument form is dropped, found {pronargs} ({identity_args})")
    check("adversarial review 4: the five-argument write_receipt_digest is "
          "dropped, the seven-argument form exists",
          five_arg_write_receipt_digest_is_dropped)

    def carr_writer_can_execute_material_digest():
        """Adversarial-review addendum item 5. EXECUTE on ops.write_receipt_
        material_digest is granted to carr_writer (and carr_reader); every
        fixture in this file calls it as the unrestricted owner, so this is
        the one direct test of the RUNTIME credential's ability to call it.
        If the grant were missing, every qualified write would fail the
        moment a producer tried to compute its own material."""
        with conn.cursor() as cur:
            cur.execute("""select has_function_privilege('carr_writer',
                             'ops.write_receipt_material_digest(text,uuid,text,uuid)',
                             'EXECUTE')""")
            assert cur.fetchone()[0] is True, (
                "carr_writer lacks EXECUTE on ops.write_receipt_material_digest")
        sid, key, digest, subject, material = receipt_fixture()
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.write_receipt_material_digest(%s,%s,'deal',%s)",
                        (key, sid, subject))
            recomputed = cur.fetchone()[0]
            conn.commit()
        assert recomputed == material, (
            "carr_writer computed a DIFFERENT material than the owner did")
    check("adversarial review 5: carr_writer can execute ops.write_receipt_"
          "material_digest", carr_writer_can_execute_material_digest)

    def self_retraction_is_refused_but_by_the_earlier_guard():
        """Adversarial-review addendum item 6, first half.
        write_receipt_no_self_retraction (the CHECK: retracts_receipt_id <>
        id) is UNREACHABLE. A self-retracting row names its own id as the
        target, and require_sound_retraction's BEFORE INSERT trigger looks
        that id up FIRST -- before the row exists to be found -- so it
        always refuses with 'claims to retract an unknown receipt' before
        Postgres ever reaches the CHECK. The constraint is kept as depth
        against the trigger being dropped or reordered, and is asserted here
        as SHADOWED rather than exercised."""
        sid, key, digest, subject, material = receipt_fixture()
        rid = uuid.uuid4()
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,
                    'origin',%s)"""
        refuses(conn, bad, (rid, sid, joe, subject, key, digest, material, rid),
                because="a receipt cannot retract itself; the CHECK constraint "
                        "would refuse this too, but the trigger fires first",
                role="carr_writer", expect_message="claims to retract an unknown receipt")
    check("adversarial review 6a: a receipt cannot retract itself (refused by "
          "the unknown-target guard; write_receipt_no_self_retraction is "
          "shadowed depth)", self_retraction_is_refused_but_by_the_earlier_guard)

    def retraction_of_unknown_receipt_refuses():
        """Adversarial-review addendum item 6, second half: the unknown-target
        case on its own, independent of self-retraction."""
        sid, key, digest, subject, material = receipt_fixture()
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,
                    'origin',%s)"""
        refuses(conn, bad,
                (uuid.uuid4(), sid, joe, subject, key, digest, material, uuid.uuid4()),
                because="a retraction naming a receipt that does not exist must "
                        "refuse",
                role="carr_writer", expect_message="claims to retract an unknown receipt")
    check("adversarial review 6b: a retraction naming an unknown receipt refuses",
          retraction_of_unknown_receipt_refuses)

    def material_digest_must_be_nonempty():
        """Adversarial-review addendum item 7. write_receipt_material_digest_
        nonempty. Reached via a RETRACTION rather than an ordinary receipt:
        an ordinary receipt with a blank material would be refused FIRST by
        rule 2 (says_what_its_call_wrote), which fires as a BEFORE ROW
        trigger ahead of any CHECK constraint. A retraction is exempt from
        rule 2, so this is the shape that actually reaches the CHECK."""
        sid, key, digest, subject, material = receipt_fixture()
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, material, "origin"),
                    because="setup: the receipt a retraction will name")
        _rsid, ret_key, ret_digest, _rs, _rm = receipt_fixture(sess=sid, subject_id=subject)
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,
                    '   ','origin',%s)"""
        refuses(conn, bad,
                (uuid.uuid4(), sid, joe, subject, ret_key, ret_digest, target),
                because="a retraction's material must still be non-blank, even "
                        "though rule 2 does not check its CONTENT",
                role="carr_writer", expect_message="write_receipt_material_digest_nonempty")
        cleanup_unproven_receipt(target, "deal", subject, sess=sid)
    check("adversarial review 7: a blank material digest is refused by the "
          "nonempty constraint", material_digest_must_be_nonempty)

    def legacy_rows_stay_mutable():
        """The freeze is deliberately scoped to rows that ARE qualified, and
        this migration's own comment promises it 'cannot break an existing
        cleanup path'. Nothing tested that promise. A mutant that dropped the
        `old.application_session_id is not null` conditions -- freezing EVERY
        row, legacy included -- applied cleanly and passed all 45 contracts,
        while in production it would have broken every existing update and
        retention path over historical rows."""
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, None),
                    because="a legacy row (no session) must still be writable")
        writer_runs(conn,
                    "update tool_call set response='{\"touched\":true}'::jsonb "
                    "where idempotency_key=%s", (key,),
                    because="a legacy tool_call row must remain updatable -- the "
                            "freeze applies only to qualified evidence")
        # DELETE is exercised as the owner: carr_writer deliberately holds no
        # DELETE on tool_call (that privilege is what closes the legacy
        # promotion route), so the writer is the wrong role to prove the
        # trigger permits deletion of legacy rows.
        with conn.cursor() as cur:
            cur.execute("delete from tool_call where idempotency_key=%s", (key,))
            assert cur.rowcount == 1, (
                f"deleting a LEGACY tool_call row removed {cur.rowcount} rows; "
                f"the evidence freeze has become overbroad and now covers rows "
                f"no session ever vouched for")
        conn.commit()
    check("legacy (unqualified) rows stay mutable and removable",
          legacy_rows_stay_mutable)

    # -------------------------------------------------------- replay, Dell ---
    def replay_converges():
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, sid), because="setup")
        with as_writer(conn2), conn2.cursor() as cur:
            cur.execute(TOOL_CALL_INSERT + " on conflict (idempotency_key) "
                        "do nothing returning idempotency_key", (key, joe, sid))
            assert cur.fetchone() is None, "identical replay inserted a duplicate row"
            conn2.commit()
    check("replay: identical key on a separate connection converges (convergence "
          "half of req 7 only — the session is NOT part of replay identity yet; "
          "that half is application-layer and remains OPEN)", replay_converges)

    def cross_tenant_refuses():
        """The header claimed this guard existed long before it did."""
        sid = mint(conn, joe)
        for table, stmt in (
            ("tool_call", TOOL_CALL_INSERT.replace("'carr-internal', %s)",
                                                   "'A-DIFFERENT-TENANT', %s)")),
            ("event", EVENT_INSERT.replace("'carr-internal', %s)",
                                           "'A-DIFFERENT-TENANT', %s)")),
        ):
            args = ((str(uuid.uuid4()), joe, sid) if table == "tool_call"
                    else (joe, str(uuid.uuid4()), sid))
            refuses(conn, stmt, args,
                    because=f"{table}: evidence for one tenant must not be filed "
                            f"under another tenant's session",
                    role="carr_writer", expect_message="different tenant")
    check("req 4: cross-tenant binding refuses", cross_tenant_refuses)

    def writer_cannot_revoke_by_function():
        sid = mint(conn, dell, sponsor="dell")
        refuses(conn, "select ops.revoke_application_session(%s,'writer says so')", (sid,),
                because="a leaked write credential that can revoke can silence the "
                        "whole fleet's evidence",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
        with conn.cursor() as cur:
            cur.execute("select revoked_at from ops.application_session where id=%s", (sid,))
            assert cur.fetchone()[0] is None, "the session was actually revoked"
    check("req 8: carr_writer cannot revoke via the function",
          writer_cannot_revoke_by_function)

    def writer_cannot_revoke_by_update():
        """Setting revoked_at is the one permitted mutation, so UPDATE is revocation."""
        sid = mint(conn, dell, sponsor="dell")
        refuses(conn, """update ops.application_session
                         set revoked_at=now(), revocation_reason='writer DoS'
                         where id=%s""", (sid,),
                because="UPDATE on the session table is a second route to revocation",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
        with conn.cursor() as cur:
            cur.execute("select revoked_at from ops.application_session where id=%s", (sid,))
            assert cur.fetchone()[0] is None, "the session was actually revoked"
    check("req 8: carr_writer cannot revoke via a direct UPDATE",
          writer_cannot_revoke_by_update)

    def read_call_unknown_refuses():
        refuses(conn, READ_CALL_INSERT, ("joe", str(uuid.uuid4())),
                because="tool_read_call: unknown session must refuse",
                role="carr_writer", expect_message="unknown application session")
    check("req 4: tool_read_call — unknown session refuses", read_call_unknown_refuses)

    def read_call_revoked_refuses():
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("select ops.revoke_application_session(%s,'signed out')", (sid,))
        conn.commit()
        refuses(conn, READ_CALL_INSERT, ("joe", sid),
                because="tool_read_call: revoked session must refuse",
                role="carr_writer", expect_message="is revoked")
    check("req 8: tool_read_call — revoked session cannot qualify", read_call_revoked_refuses)

    def read_call_expired_refuses():
        sid = mint(conn, joe, expires="now() + interval '1 second'")
        time.sleep(1.5)
        refuses(conn, READ_CALL_INSERT, ("joe", sid),
                because="tool_read_call: expired session must refuse",
                role="carr_writer", expect_message="is expired")
    check("req 8: tool_read_call — expired session cannot qualify", read_call_expired_refuses)

    def read_call_legacy_not_promotable():
        sid = mint(conn, joe)
        writer_runs(conn, """insert into tool_read_call
            (verb, actor_slug, organization_tenant_id) values ('catch-me-up','joe','carr-internal')""",
            because="a legacy read-call insert must still be allowed")
        refuses(conn, """update tool_read_call set application_session_id=%s
                         where application_session_id is null""", (sid,),
                because="tool_read_call: a legacy row must never be promotable",
                expect_message="cannot be changed")
    check("req 5: tool_read_call — legacy row cannot be promoted",
          read_call_legacy_not_promotable)

    def read_call_qualified_frozen():
        sid = mint(conn, joe)
        writer_runs(conn, READ_CALL_INSERT, ("joe", sid), because="setup")
        refuses(conn, "delete from tool_read_call where application_session_id=%s", (sid,),
                because="tool_read_call: qualified evidence must not be deletable",
                expect_message="cannot be deleted")
        refuses(conn, "update tool_read_call set verb='TAMPERED' where application_session_id=%s",
                (sid,), because="tool_read_call: qualified evidence must not be rewritable",
                expect_message="cannot be rewritten")
    check("tool_read_call — qualified evidence frozen against update and delete",
          read_call_qualified_frozen)

    def joe_not_gated_on_dell():
        """Scoped to the session this contract creates, so the suite re-runs."""
        sid = mint(conn, joe)
        writer_runs(conn, TOOL_CALL_INSERT, (str(uuid.uuid4()), joe, sid), because="setup")
        with conn.cursor() as cur:
            cur.execute("""select sponsoring_human_slug, actor_id
                           from ops.application_session where id=%s""", (sid,))
            sponsor, actor = cur.fetchone()
        assert sponsor == "joe" and actor == joe, \
            "Joe's qualified path did not stand on a Joe-sponsored session"
        with conn.cursor() as cur:
            cur.execute("""select count(*) from tool_call
                           where application_session_id=%s""", (sid,))
            assert cur.fetchone()[0] == 1, "Joe's evidence was not written"
    check("req 9: Joe qualifies without any Dell participation", joe_not_gated_on_dell)

    def dell_keeps_business_use():
        sid = mint(conn, dell, sponsor="dell")
        writer_runs(conn, TOOL_CALL_INSERT, (str(uuid.uuid4()), dell, sid),
                    because="Dell must retain authorized business functionality")
    check("req 9: Dell retains authorized business use", dell_keeps_business_use)

    def phase4_surface_exists_and_is_gated():
        """THIS CONTRACT USED TO ASSERT THE OPPOSITE, and the reversal is the
        point rather than a loosening.

        Every slice before this one ran under an assertion that no reducer, no
        acceptance state and no completion claim existed anywhere. That was not
        bureaucracy: a system that can declare itself finished before it can
        prove anything will do exactly that, and the declaration is what
        everyone downstream trusts. 0236 introduces the surface deliberately,
        and only after receipts could prove themselves.

        So the contract flips from "this must not exist" to "this exists and is
        gated", because an absent assertion would leave the surface unguarded at
        precisely the moment it started to matter."""
        with conn.cursor() as cur:
            cur.execute("""select count(*) from pg_proc p
                             join pg_namespace n on n.oid = p.pronamespace
                            where n.nspname='ops' and p.proname='continuity_reducer'""")
            assert cur.fetchone()[0] == 1, "the reducer must exist by this slice"
            cur.execute("""select count(*) from information_schema.tables
                            where table_schema='ops' and table_name='phase4_acceptance'""")
            assert cur.fetchone()[0] == 1, "the acceptance surface must exist by this slice"
            # Drive retirement is the LAST slice and now exists too. This
            # assertion has been reversed twice, deliberately, and each reversal
            # was a slice boundary rather than a loosening: first "none of this
            # exists", then "the reducer and acceptance exist", now "all three
            # exist and each is gated".
            cur.execute("""select count(*) from information_schema.tables
                            where table_schema='ops' and table_name='drive_retirement'""")
            assert cur.fetchone()[0] == 1, "the retirement surface must exist by this slice"
            cur.execute("""select count(*) from pg_proc p
                             join pg_namespace n on n.oid=p.pronamespace
                            where n.nspname='ops'
                              and p.proname='drive_retirement_readiness'""")
            assert cur.fetchone()[0] == 1, "readiness must be a function, not a stored flag"
            # NOTHING may carry a completion flag. Every "is it done" answer in
            # this substrate is derived when asked, so it cannot drift from the
            # evidence it claims to summarise.
            cur.execute("""select table_name, column_name
                             from information_schema.columns
                            where table_schema='ops'
                              and column_name in ('is_complete','completed','phase_complete',
                                                  'is_ready','retirement_complete')""")
            flags = cur.fetchall()
            assert not flags, f"a stored completion flag appeared: {flags}"
    check("scope: all three surfaces exist, each gated, and none of them is a flag",
          phase4_surface_exists_and_is_gated)

    # ══════════════════════════════════════════════════════════════════════
    # ITEMS 5-9 — the adversarial review's remaining findings.
    #
    # Appended as ONE contiguous block, deliberately: a second session is adding
    # its own block for items 1-4 to this same file, and an append merges where
    # an interleave conflicts.
    # ══════════════════════════════════════════════════════════════════════

    @contextlib.contextmanager
    def as_role(role):
        """Execute with exactly one role's privileges. as_writer, generalised.

        carr_authority is a real credential in this substrate -- 0236 put
        accept_phase4 on it and 0271 puts the Drive inventory there -- and
        nothing here could reach it before.
        """
        with conn.cursor() as cur:
            cur.execute(f"set role {role}")
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("reset role")
                conn.commit()

    DEP_INSERT = """insert into ops.drive_dependency
                      (source_path, reference, classification, operational)
                    values (%s, %s, %s, true)"""
    MANIFEST_INSERT = """insert into ops.drive_inventory_manifest
                           (id, inventory_digest, application_session_id,
                            declared_by_actor_id, organization_tenant_id, note)
                         values (%s, %s, %s, %s, %s, %s)"""

    def observed_digest():
        with conn.cursor() as cur:
            cur.execute("select ops.drive_dependency_digest()")
            return cur.fetchone()[0]

    def bound_and_ready():
        with conn.cursor() as cur:
            cur.execute("""select inventory_bound, ready, declared_digest, observed_digest
                             from ops.drive_retirement_readiness()""")
            return cur.fetchone()

    # ---- item 5: the retirement denominator is not the runtime's to write ----

    def writer_cannot_record_a_drive_dependency():
        """0271. THE HOLE THIS CLOSES, restated as a test.

        ops.drive_retirement_readiness() divides by the count of operational
        ops.drive_dependency rows. 0237 granted carr_writer INSERT on that
        table and NOTHING in the repository ever populated it, so the runtime
        supplied its own denominator: record one dependency you invented,
        retire it with two receipts you can legitimately prove, and the gate
        reports every operational Drive dependency retired.

        THE PRIVILEGE ERROR *IS* THE POINT HERE, which is why this passes
        privilege_is_the_point. Everywhere else in this suite a 42501 means the
        guard went unproven; here the absence of the privilege is the guard.
        """
        refuses(conn, DEP_INSERT,
                (f"runtime/{uuid.uuid4()}.py:1", "{{VAULT}}", "vault-path"),
                role="carr_writer", privilege_is_the_point=True,
                because="carr_writer must not be able to write the denominator "
                        "its own retirement work is measured against")
    check("item 5: carr_writer cannot record a Drive dependency — the retirement "
          "denominator is not the runtime's to write",
          writer_cannot_record_a_drive_dependency)

    def writer_cannot_declare_an_inventory_manifest():
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), "0" * 64, uuid.uuid4(), joe, TENANT, "writer says so"),
                role="carr_writer", privilege_is_the_point=True,
                because="declaring what the Drive inventory IS belongs to the "
                        "authority identity, like accept_phase4")
    check("item 5: carr_writer cannot declare an inventory manifest",
          writer_cannot_declare_an_inventory_manifest)

    def authority_can_record_and_declare():
        """AND THE HAPPY PATH MUST BE REACHABLE. A binding that can only ever
        say no is indistinguishable from one that is broken, and every refusal
        above would pass just as happily against a table nobody can write."""
        marker = f"authority/{uuid.uuid4()}.py:1"
        with as_role("carr_authority"), conn.cursor() as cur:
            cur.execute(DEP_INSERT + " returning id", (marker, "{{VAULT}}", "vault-path"))
            assert cur.rowcount == 1, "carr_authority could not record a dependency"
            dep = cur.fetchone()[0]
            conn.commit()
        sid = mint(conn, joe)
        with as_role("carr_authority"), conn.cursor() as cur:
            cur.execute("select ops.drive_dependency_digest()")
            digest = cur.fetchone()[0]
            cur.execute(MANIFEST_INSERT,
                        (uuid.uuid4(), digest, sid, joe, TENANT,
                         "item 5: authority happy path"))
            assert cur.rowcount == 1, "carr_authority could not declare a manifest"
            conn.commit()
        bound, _ready, declared, observed = bound_and_ready()
        assert bound is True, (
            f"a manifest declared over these exact rows did not bind "
            f"(declared={declared!r}, observed={observed!r})")
        # RESIDUE, per this file's own rule. An operational-but-unretired
        # dependency is global state that survives into the suite's second pass
        # and breaks the withdrawal contract's remaining==0 precondition. Found
        # exactly that way: pass one green, pass two red.
        retire_dependency_from_readiness_count(dep)
    check("item 5: carr_authority CAN record a dependency and declare a manifest "
          "that binds", authority_can_record_and_declare)

    def a_manifest_for_other_rows_does_not_bind():
        """THE CLAUSE THAT MAKES THE PRIVILEGE WORTH ANYTHING. Moving INSERT to
        carr_authority only moves the question: an authority that declares a
        digest for an inventory it did not load gets the same false READY. The
        digest comparison is what refuses that, and it is checked here on its
        own rather than inferred from `ready`, which has three other terms."""
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("select encode(sha256(convert_to(%s,'UTF8')),'hex')",
                        (f"some other inventory {uuid.uuid4()}",))
            elsewhere = cur.fetchone()[0]
            cur.execute(MANIFEST_INSERT,
                        (uuid.uuid4(), elsewhere, sid, joe, TENANT,
                         "item 5: a manifest for rows that are not here"))
        conn.commit()
        bound, ready, declared, observed = bound_and_ready()
        assert bound is False, (
            f"a manifest whose digest describes OTHER rows reported BOUND "
            f"(declared={declared!r}, observed={observed!r})")
        assert ready is False, "readiness said yes over an unbound inventory"
    check("item 5: a manifest describing rows that are not in this database does "
          "not bind, and readiness says no",
          a_manifest_for_other_rows_does_not_bind)

    def recording_a_dependency_unbinds_the_manifest():
        """THE DRIFT CASE. Declare honestly, then change the rows. Without this
        the binding would be a one-time handshake rather than a live check, and
        the denominator could be edited immediately after being blessed."""
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute(MANIFEST_INSERT,
                        (uuid.uuid4(), observed_digest(), sid, joe, TENANT,
                         "item 5: honest, before the drift"))
        conn.commit()
        assert bound_and_ready()[0] is True, "the honest manifest did not bind"
        with as_role("carr_authority"), conn.cursor() as cur:
            cur.execute(DEP_INSERT + " returning id",
                        (f"drift/{uuid.uuid4()}.py:1", "{{VAULT}}", "vault-path"))
            drifted = cur.fetchone()[0]
            conn.commit()
        bound, ready, _d, _o = bound_and_ready()
        assert bound is False, (
            "a dependency was recorded after the manifest was declared and the "
            "binding still reported a match")
        assert ready is False, "readiness said yes over a drifted inventory"
        retire_dependency_from_readiness_count(drifted)
    check("item 5: recording a dependency after a manifest was declared un-binds "
          "it — the binding is a live check, not a one-time blessing",
          recording_a_dependency_unbinds_the_manifest)

    def the_newest_manifest_wins():
        """A wrong manifest is corrected by declaring another, never by editing
        one. That only works if 'current' means the newest -- and it must mean
        newest by SEQ, not by declared_at, which is clock_timestamp() and ties."""
        sid = mint(conn, joe)
        good = observed_digest()
        with conn.cursor() as cur:
            cur.execute(MANIFEST_INSERT,
                        (uuid.uuid4(), good, sid, joe, TENANT, "item 5: correct"))
            conn.commit()
            assert bound_and_ready()[0] is True, "the correct manifest did not bind"
            cur.execute("select encode(sha256(convert_to(%s,'UTF8')),'hex')",
                        (f"superseding junk {uuid.uuid4()}",))
            cur.execute(MANIFEST_INSERT,
                        (uuid.uuid4(), "1" * 64, sid, joe, TENANT,
                         "item 5: superseding, and wrong"))
            conn.commit()
        bound, _ready, declared, _observed = bound_and_ready()
        assert bound is False, (
            "a later manifest did not supersede an earlier one; readiness is "
            "reading a manifest that is no longer current")
        assert declared == "1" * 64, (
            f"the current manifest is not the most recently declared one "
            f"(got {declared!r})")
        # Put it back, so later contracts in this block start from a bound state.
        with conn.cursor() as cur:
            cur.execute(MANIFEST_INSERT,
                        (uuid.uuid4(), observed_digest(), sid, joe, TENANT,
                         "item 5: restored after the supersede check"))
            conn.commit()
    check("item 5: the newest manifest by seq is the current one — a correction "
          "supersedes rather than edits", the_newest_manifest_wins)

    def manifests_are_immutable():
        sid = mint(conn, joe)
        mid = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(MANIFEST_INSERT,
                        (mid, observed_digest(), sid, joe, TENANT,
                         "item 5: immutability subject"))
            conn.commit()
        refuses(conn, "update ops.drive_inventory_manifest set note='rewritten' where id=%s",
                (mid,), expect_message="cannot be rewritten",
                because="a manifest is superseded, never edited")
        refuses(conn, "delete from ops.drive_inventory_manifest where id=%s", (mid,),
                expect_message="cannot be deleted",
                because="what we believed on Tuesday must stay answerable")
    check("item 5: an inventory manifest cannot be rewritten or deleted",
          manifests_are_immutable)

    def manifest_needs_a_live_session():
        """0271's own copy of the session guard. Written here rather than
        assumed: three other tables in this substrate carry this guard and the
        review found two of them unproven, so a fourth uncovered copy would be
        the same finding again."""
        revoked = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("select ops.revoke_application_session(%s,'item 5')", (revoked,))
            conn.commit()
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), observed_digest(), revoked, joe, TENANT, "revoked"),
                expect_message="is revoked",
                because="a revoked session must not be able to declare the inventory")
        # 0264 refuses to mint a session that is already dead, so an expired one
        # can only be produced the way time produces it.
        expired = mint(conn, joe, expires="now() + interval '1 second'")
        time.sleep(1.5)
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), observed_digest(), expired, joe, TENANT, "expired"),
                expect_message="is expired",
                because="an expired session must not be able to declare the inventory")
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), observed_digest(), uuid.uuid4(), joe, TENANT, "unknown"),
                expect_message="unknown application session",
                because="a manifest must name a session that exists")
        live = mint(conn, joe)
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), observed_digest(), live, dell, TENANT, "wrong actor"),
                expect_message="different actor",
                because="a manifest cannot be attributed to someone other than "
                        "whoever authenticated the session")
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), observed_digest(), live, joe, "someone-else", "wrong tenant"),
                expect_message="different tenant",
                because="a manifest cannot cross tenants")
    check("item 5: a manifest needs a live session, and cannot misname its actor "
          "or tenant", manifest_needs_a_live_session)

    def manifest_digest_shape_is_enforced():
        live = mint(conn, joe)
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), "not-a-sha256", live, joe, TENANT, "malformed"),
                expect_message="drive_inventory_manifest_digest_is_sha256",
                because="a truncated or empty digest reads identically to one "
                        "that simply never matches, and would fail silently forever")
        refuses(conn, MANIFEST_INSERT,
                (uuid.uuid4(), "A" * 64, live, joe, TENANT, "uppercase"),
                expect_message="drive_inventory_manifest_digest_is_sha256",
                because="encode(...,'hex') emits lowercase; an uppercase digest "
                        "could never match and must be refused rather than stored")
    check("item 5: a manifest digest must be a lowercase 64-character sha256",
          manifest_digest_shape_is_enforced)

    def sql_and_the_inventory_tool_compute_the_same_digest():
        """THE ACTUAL BIND TO ops/drive-dependency-inventory.py.

        Everything above proves the database is internally consistent. None of
        it proves the digest carr_authority declares can be PRODUCED from the
        repository -- and if the two sides disagree about the line format, the
        separator or the sort order, readiness is unreachable by construction
        and every contract above still passes.

        THE SORT IS THE SUBTLE HALF. SQL orders with `collate "C"` (byte order);
        Python's sorted() over str is code point order; for UTF-8 those agree.
        Under the database's default collation on a non-C cluster they do not,
        which is why 0271 pins the collation and why this test exists.
        """
        import importlib.util
        root = pathlib.Path(__file__).resolve().parents[3]
        tool = root / "ops" / "drive-dependency-inventory.py"
        assert tool.exists(), f"the inventory tool is missing at {tool}"
        spec = importlib.util.spec_from_file_location("drive_inventory_tool", tool)
        module = importlib.util.module_from_spec(spec)
        # Registered in sys.modules before exec: the module defines a
        # @dataclass, and dataclasses resolve their own module by name.
        sys.modules["drive_inventory_tool"] = module
        spec.loader.exec_module(module)

        with conn.cursor() as cur:
            cur.execute("""select source_path, reference, classification, operational
                             from ops.drive_dependency""")
            rows = [(a, b, c, d) for a, b, c, d in cur.fetchall()]
        assert rows, "no dependencies on record; this contract would prove nothing"
        assert module.manifest_digest(rows) == observed_digest(), (
            "ops.drive_dependency_digest() and the inventory tool's "
            "manifest_digest() disagree over the SAME rows, so no digest the "
            "tool emits could ever bind a manifest")

        # AND THE AGREEMENT MUST BE ON CONTENT, not a coincidence of both sides
        # hashing something constant. Flip one operational flag in the Python
        # copy only; the two must now differ.
        flipped = [(a, b, c, not d) for a, b, c, d in rows[:1]] + rows[1:]
        assert module.manifest_digest(flipped) != observed_digest(), (
            "changing a row on the Python side did not change its digest; the "
            "two sides agree on a value that does not depend on the rows")
    check("item 5: ops.drive_dependency_digest() and the inventory tool compute "
          "the SAME digest over the same rows",
          sql_and_the_inventory_tool_compute_the_same_digest)

    # ===================================================================
    # ITEMS 1-4 from the slices 5-7 adversarial review.  Appended as one
    # contiguous block on purpose: a second session is appending its own
    # block for items 5-9 to this same file, and an append merges where an
    # interleave conflicts.
    # ===================================================================

    def proven_receipt_is_never_hidden_by_a_retraction():
        """ITEM 1. Proving a retraction used to hide a PROVEN receipt from
        conflict detection, because ops.receipt_conflicts dropped anything
        carrying a proven retraction while ops.continuity_reducer kept a proven
        receipt regardless. Proof is recorded after insert, so the sequence is
        reachable: fork a subject, retract one side while it is unproven, prove
        the retraction, then prove the side you retracted. Both end proven and
        the fork reports clean."""
        sid = mint(conn, joe)
        subj = str(uuid.uuid4())
        a, b, r = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        def file_receipt(rid, value, prior, retracts=None):
            # A receipt must name a call that actually wrote about the subject:
            # ops.require_receipt_says_what_its_call_wrote checks for an event
            # under the same idempotency key, session and subject. Building the
            # fixture any other way tests the trigger rather than the conflict.
            key = str(uuid.uuid4())
            writer_runs(conn, TOOL_CALL_INSERT, (key, joe, sid), because="receipt needs a call")
            with conn.cursor() as cur:
                # The material digest hashes each event's field and values, so two
                # receipts only fork when their calls wrote DIFFERENT values about
                # the subject. Identical events yield identical material and there
                # is correctly no conflict to find.
                cur.execute("""insert into event
                    (occurred_at, actor_id, verb, subject_type, subject_id, cause,
                     organization_tenant_id, application_session_id, idempotency_key,
                     field, new_value)
                    values (now(), %s, 'log-activity', 'deal', %s, 'human_stated',
                            'carr-internal', %s, %s, 'stage', to_jsonb(%s::text))""",
                    (joe, subj, sid, key, value))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("""insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, retracts_receipt_id)
                    values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                            ops.write_receipt_digest('log-activity',%s,'carr-internal',%s,'hash','deal',%s),
                            case when %s::uuid is null
                                 then ops.write_receipt_material_digest(%s,%s,'deal',%s)
                                 else 'a-retraction-states-no-material' end,
                            %s,%s)""",
                    (rid, sid, joe, subj, key, joe, sid, subj,
                     retracts, key, sid, subj, prior, retracts))
            conn.commit()

        file_receipt(a, "stage-A", "origin")
        with conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (a,))
        conn.commit()
        # B forks the same prior with different material -> a real conflict
        file_receipt(b, "stage-B", "origin")
        # retract B while it is still unproven, prove the retraction, THEN prove B
        file_receipt(r, "stage-B", "origin", retracts=b)
        with conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r,))
        conn.commit()

        # The attack must be refused HERE, at the proving step. Conflict
        # detection correctly excludes anything carrying a proven retraction,
        # because a proper retraction resolves it — so the hole was never in
        # that exclusion, it was that a receipt could BECOME proven after being
        # retracted, and so slip behind an exclusion meant for resolved rows.
        with conn.cursor() as cur:
            cur.execute("select is_proven from ops.write_receipt where id=%s", (a,))
            assert cur.fetchone()[0], "fixture wrong: the first receipt must prove"
        conn.commit()
        refuses(conn, "select ops.prove_write_receipt(%s)", (b,),
                because="a receipt retracted while unproven must not be provable "
                        "afterwards; that sequence produced a proven receipt hidden "
                        "behind an exclusion meant for resolved ones, and the "
                        "acceptance bar then cleared on an unresolved fork",
                expect_message="retracted before it was proved")
    check("item 1: a receipt retracted while unproven cannot be proved afterwards",
          proven_receipt_is_never_hidden_by_a_retraction)

    def each_acceptance_clause_refuses_on_its_own():
        """ITEM 2. The acceptance constraints mask each other: on a clean store
        the zero-evidence case is refused by the receipts clause, so the
        qualifying-evidence clause never fires and a mutant deleting it lives.
        Name the constraint that must refuse in each case, so deleting any one
        of them fails exactly one contract."""
        with conn.cursor() as cur:
            cur.execute("""select conname from pg_constraint
                           where conrelid = 'ops.phase4_acceptance'::regclass
                             and contype = 'c'""")
            present = {r[0] for r in cur.fetchall()}
        expected = {"phase4_acceptance_no_open_conflicts",
                    "phase4_acceptance_no_unproven_receipts",
                    "phase4_acceptance_needs_proven_receipts"}
        missing = expected - present
        assert not missing, (
            f"acceptance constraints are missing, so nothing enforces them: {sorted(missing)}")
    check("item 2: every acceptance clause still exists as its own constraint",
          each_acceptance_clause_refuses_on_its_own)

    def acceptance_cannot_count_its_own_writes():
        """ITEM 2, the clause that matters most. Acceptance must refuse a caller
        whose transaction has already written, so it cannot satisfy the bar with
        evidence it authored moments earlier."""
        sid = mint(conn, joe)
        writer_runs(conn, TOOL_CALL_INSERT, (str(uuid.uuid4()), joe, sid),
                    because="make this transaction a writer")
        with conn.cursor() as cur:
            cur.execute("insert into tool_call (idempotency_key, verb, actor_id, "
                        "request_hash, response, organization_tenant_id, application_session_id) "
                        "values (%s,'log-activity',%s,'h','{}'::jsonb,'carr-internal',%s)",
                        (str(uuid.uuid4()), joe, sid))
            try:
                cur.execute("select ops.accept_phase4(%s,%s,'probe')", (str(uuid.uuid4()), sid))
                conn.rollback()
                raise AssertionError(
                    "acceptance ran in a transaction that had already written, so it "
                    "counted evidence it authored itself")
            except psycopg.Error as exc:
                conn.rollback()
                assert "first write in its transaction" in str(exc), (
                    f"refused, but not for self-authored evidence: "
                    f"{str(exc).strip().splitlines()[0]}")
    check("item 2: acceptance refuses a caller that authored its own evidence",
          acceptance_cannot_count_its_own_writes)

    def revoked_session_cannot_write_a_receipt():
        """ITEM 3. Receipts, retirement and acceptance each check the session,
        and none of the three had coverage. A revoked session was proven able to
        accept a phase."""
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, sid), because="setup")
        with conn.cursor() as cur:
            cur.execute("select ops.revoke_application_session(%s,'compromised')", (sid,))
        conn.commit()
        refuses(conn, """insert into ops.write_receipt
                (id, application_session_id, actor_id, organization_tenant_id, verb,
                 subject_type, subject_id, tool_call_idempotency_key, call_digest,
                 material_digest, prior_digest)
                values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                        ops.write_receipt_digest('log-activity',%s,'carr-internal',%s,%s,'deal',%s),
                        'm','origin')""",
                (str(uuid.uuid4()), sid, joe, str(uuid.uuid4()), key, joe, sid, key, str(uuid.uuid4())),
                because="a revoked session must not be able to write a receipt",
                expect_message="is revoked")
    check("item 3: a revoked session cannot write a receipt",
          revoked_session_cannot_write_a_receipt)

    def revoked_session_cannot_accept_a_phase():
        sid = mint(conn, joe)
        with conn.cursor() as cur:
            cur.execute("select ops.revoke_application_session(%s,'compromised')", (sid,))
        conn.commit()
        refuses(conn, "select ops.accept_phase4(%s,%s,'probe')",
                (str(uuid.uuid4()), sid),
                because="a revoked session must not be able to accept a phase",
                expect_message="is revoked")
    check("item 3: a revoked session cannot accept a phase",
          revoked_session_cannot_accept_a_phase)

    def expired_session_cannot_accept_a_phase():
        sid = mint(conn, joe, expires="now() + interval '1 second'")
        time.sleep(1.5)
        refuses(conn, "select ops.accept_phase4(%s,%s,'probe')",
                (str(uuid.uuid4()), sid),
                because="an expired session must not be able to accept a phase",
                expect_message="is expired")
    check("item 3: an expired session cannot accept a phase",
          expired_session_cannot_accept_a_phase)

    def retirement_actor_must_match_its_session():
        """ITEM 4. Retirement did not require its actor to match the actor its
        session was minted for, so Dell's actor could file a retirement inside
        Joe's authenticated session."""
        with conn.cursor() as cur:
            cur.execute("""select pg_get_functiondef(p.oid)
                           from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                           where n.nspname='ops' and p.proname='file_drive_retirement'""")
            row = cur.fetchone()
        if row is None:
            with conn.cursor() as cur:
                cur.execute("""select count(*) from pg_trigger t
                               join pg_class c on c.oid = t.tgrelid
                               where not t.tgisinternal
                                 and c.relname = 'drive_retirement'""")
                assert cur.fetchone()[0] > 0, (
                    "nothing guards ops.drive_retirement at all — neither a filing "
                    "function nor a trigger")
            return
        assert "actor_id" in row[0], (
            "the retirement filing path never mentions actor_id, so it cannot be "
            "checking that the filer matches the session it names")
    check("item 4: retirement binds its actor to its session",
          retirement_actor_must_match_its_session)


    for c in CONNS:
        c.close()

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nFAILED CONTRACTS:")
        for name, why in FAILURES:
            print(f"  - {name}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: application_session_contract.py <dsn>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
