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
    %s, %s, 'carr-internal', %s, 'dealroom-cookie',
    'accounts.google.com', 'human_partner', 'joe@example.test', {expires})"""

TENANT = "carr-internal"

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


def mint(conn, actor, expires="now() + interval '1 hour'", sponsor="joe"):
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(MINT.format(expires=expires), (sid, actor, sponsor))
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
            f"0204 makes permanently undeletable; it may only target a disposable "
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
                            (sid, joe, "joe"))
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
        refuses(conn, MINT.format(expires="now() + interval '1 hour'"), (sid, joe, "joe"),
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
                (uuid.uuid4(), joe, "joe"),
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

    def triggers_enable_always():
        with conn.cursor() as cur:
            cur.execute("""select c.relname, t.tgname, t.tgenabled
                           from pg_trigger t join pg_class c on c.oid=t.tgrelid
                           where not t.tgisinternal
                             and (t.tgname like '%application_session%'
                                  or t.tgname like '%requires_live_session%'
                                  or t.tgname like '%qualified_evidence%')""")
            rows = cur.fetchall()
        assert len(rows) >= 10, f"expected at least 10 guard triggers, found {len(rows)}"
        weak = [(r[0], r[1]) for r in rows if r[2] != "A"]
        assert not weak, (f"not ENABLE ALWAYS, so session_replication_role='replica' "
                          f"switches them off: {weak}")
    check("every session trigger is ENABLE ALWAYS", triggers_enable_always)

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

    def no_phase4_surface_introduced():
        forbidden = ("continuity_reducer", "phase4_acceptance", "drive_retirement")
        with conn.cursor() as cur:
            cur.execute("""select table_name from information_schema.tables
                           where table_schema in ('ops','public')""")
            names = {r[0] for r in cur.fetchall()}
        hit = [f for f in forbidden if any(f in n for n in names)]
        assert not hit, f"this slice introduced out-of-scope Phase 4 surface: {hit}"
    check("scope: no reducer, acceptance, or retirement surface added",
          no_phase4_surface_introduced)

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
