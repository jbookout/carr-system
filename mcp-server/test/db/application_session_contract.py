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
            f"0208 makes permanently undeletable; it may only target a disposable "
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

    # ------------------------------------------- 0209: the minting credential ----
    # 0208 left carr_session_minter memberless ON PURPOSE and said so; 0209 is
    # the decision about which credential joins it. 0209's own apply-time block
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
                            (sid, joe, "joe"))
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
    check("req 1: carr_session_issuer CAN mint (the credential 0209 chose)",
          issuer_can_actually_mint)

    def issuer_cannot_write_evidence():
        """The separation runs BOTH ways, and this is the half that is easy to
        forget. If the issuer could also insert evidence, one leaked secret
        would mint a session AND bind rows to it, which is the whole attack
        0208 exists to prevent -- just performed with a different credential."""
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

    # ------------------------------------- 0210: minting from an actor slug ----
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
    check("req 1: the issuer can mint from a slug (0210 — the door has no actor id)",
          issuer_can_mint_by_slug)

    def unknown_slug_refuses():
        """A session with a null actor would satisfy 'a row exists' while failing
        the only thing the row is for: 0208's guard matches the evidence row's
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
        exactly what 0209 spent a role separating it from."""
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

    # ---------------------------------------------- 0211: write receipts ----
    # This layer was REJECTED once, for three things. Each contract below names
    # which rejection it answers, and every one acts as carr_writer rather than
    # as the superuser the harness hands you.
    def receipt_fixture(sess=None, key=None, req_hash="req-hash-A", subject_type="deal",
                         subject_id=None):
        """A live session plus one qualified tool_call row, written as the
        writer. Returns (session_id, idempotency_key, call_digest, subject_id).

        THE DIGEST IS SUBJECT-BOUND (0220): ops.write_receipt_digest now takes
        the receipt's own subject_type/subject_id as part of its input, so a
        digest computed for one subject cannot prove a receipt naming another.
        A caller that wants the receipt it files to actually PROVE must name
        THIS subject_type and THIS subject_id on that receipt -- never a fresh
        one invented separately."""
        sid = sess or mint(conn, joe)
        k = key or str(uuid.uuid4())
        subj = subject_id if subject_id is not None else uuid.uuid4()
        writer_runs(conn, TOOL_CALL_INSERT.replace("'hash'", f"'{req_hash}'"),
                    (k, joe, sid), because="receipt fixture needs qualified evidence")
        with conn.cursor() as cur:
            cur.execute("""select ops.write_receipt_digest('log-activity', %s,
                             'carr-internal', %s, %s, %s, %s)""",
                        (joe, sid, req_hash, subject_type, subj))
            digest = cur.fetchone()[0]
        return sid, k, digest, subj

    RECEIPT_INSERT = """insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
        values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,%s)"""

    # Same shape, over a subject_type of 'drive_dependency' -- needed once 0220
    # binds a retirement receipt's proof to naming the dependency it is about.
    RECEIPT_INSERT_DEP = RECEIPT_INSERT.replace("'deal'", "'drive_dependency'")

    # ---------------------------------------------------- 0220: the split ----
    def retraction_clears_the_acceptance_bar():
        """0220 (C). The escape hatch is a PROVEN retraction, not a delete or
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
        requiring at least one proven receipt to exist at all."""
        sid, key, digest, subject = receipt_fixture()
        anchor = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (anchor, sid, joe, subject, key, digest, "m-anchor-c", "origin"),
                    because="setup: an anchor receipt so proven_receipts > 0 already "
                            "holds before the refusal below")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (anchor,))
            assert cur.fetchone()[0] is True, "the anchor receipt must prove"
            conn.commit()

        bad_subject = uuid.uuid4()
        bad_key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (bad_key, joe, sid),
                    because="setup: the call the deliberately-unproven receipt names")
        bad = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (bad, sid, joe, bad_subject, bad_key,
                     "a-digest-nobody-ever-wrote", "m-bad", "origin"),
                    because="setup: a receipt left deliberately unproven")
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
            cur.execute("""select id, subject_type, subject_id, material_digest
                             from ops.write_receipt w
                            where not w.is_proven
                              and not exists (
                                select 1 from ops.write_receipt rr
                                 where rr.retracts_receipt_id = w.id and rr.is_proven)""")
            to_sweep = cur.fetchall()
        for target_id, subj_type, subj_id, target_material in to_sweep:
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
                          %s,%s,%s)""",
                        (rid, sid, joe, subj_type, subj_id, sweep_key, sweep_digest,
                         f"m-swept-{rid}", target_material, target_id),
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
        """0220 (C)'s other half. If an UNPROVEN retraction could clear the
        bar, the escape hatch could be opened from inside by asserting the
        same thing twice -- a retraction is only as good as its own proof.

        NEITHER `bad` NOR `ret` IS EVER COMMITTED. Both must stay unproven
        for this contract to mean anything (bad is the receipt threatening to
        block acceptance; ret, retracting it, must fail to excuse it), and
        the new prior-existence guard (section E) forces ret's prior to be
        either 'origin' (which bad already claims, so they would conflict
        with each other immediately) or bad's own material (which a later
        system-wide sweep would ALSO have to use to retract bad, colliding
        with ret at that point instead). Either way, committing this pair
        makes a permanent conflict unavoidable somewhere down the line, and
        this contract does not need either row to survive its own
        assertions -- so the whole fixture runs inside one open transaction
        and is rolled back at the end. It also proves its own anchor
        receipt first, for the same reason as the contract above."""
        sid, key, digest, subject = receipt_fixture()
        with as_writer(conn), conn.cursor() as cur:
            anchor = uuid.uuid4()
            cur.execute(RECEIPT_INSERT,
                        (anchor, sid, joe, subject, key, digest, "m-anchor-d", "origin"))
            cur.execute("select ops.prove_write_receipt(%s)", (anchor,))
            assert cur.fetchone()[0] is True, "the anchor receipt must prove"

            bad_subject = uuid.uuid4()
            bad_key = str(uuid.uuid4())
            cur.execute(TOOL_CALL_INSERT, (bad_key, joe, sid))
            bad = uuid.uuid4()
            cur.execute(RECEIPT_INSERT,
                        (bad, sid, joe, bad_subject, bad_key,
                         "a-digest-nobody-ever-wrote-either", "m-bad-d", "origin"))
            ret_key = str(uuid.uuid4())
            cur.execute(TOOL_CALL_INSERT, (ret_key, joe, sid))
            ret = uuid.uuid4()
            cur.execute("""insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, retracts_receipt_id)
                  values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                          'a-digest-never-computed','m-ret-d',%s,%s)""",
                        (ret, sid, joe, bad_subject, ret_key, "m-bad-d", bad))

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
                          'm-ret2-d',%s,%s)""",
                        (ret2, sid, joe, bad_subject, ret2_key, ret2_digest,
                         "m-ret-d", ret))
            cur.execute("select ops.prove_write_receipt(%s)", (ret2,))
            assert cur.fetchone()[0] is True, \
                "the second-level retraction must itself prove, or this contract " \
                "cannot tell the two readings apart"

            # DROP THE WRITER ROLE FOR THE ACCEPTANCE CALL, WITHOUT LEAVING
            # THE TRANSACTION. carr_writer holds no EXECUTE on accept_phase4
            # (0213 puts it on carr_authority), so calling it as the writer
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
                assert "phase4_acceptance_no_unproven_receipts" in str(exc), (
                    f"refused, but by a different bar: {str(exc).strip().splitlines()[0]}")
            conn.rollback()
    check("req 6: an unproven retraction clears nothing",
          unproven_retraction_clears_nothing)

    def retraction_must_match_subject():
        """0220 (C)'s structural half. A retraction that could disavow a claim
        about a DIFFERENT subject would let a caller clear the bar for one
        subject by pointing at unrelated evidence."""
        sid, key, digest, subject = receipt_fixture()
        target = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (target, sid, joe, subject, key, digest, "m-target-e", "origin"),
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
    check("req 6: a retraction must name the same subject as what it retracts",
          retraction_must_match_subject)

    def receipt_cannot_reverse_and_retract():
        """0220's xor constraint. Reversal and retraction mean different
        things -- one restores a subject's state, the other disavows a claim
        -- and a row satisfying both sets of rules at once is not sound.

        r1 AND r2 ARE BOTH PROVEN. Left unproven, r2 -- which builds on r1's
        own material as its prior -- is exactly the shape 0220's global,
        unscoped acceptance-bar sweep would later retract r1 into, using
        that SAME material as the retraction's prior and colliding with r2
        for real. Proving both removes them from ever being swept."""
        sid, key, digest, subject = receipt_fixture()
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subject, key, digest, "m-f1", "origin"),
                    because="setup: first receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, "the first receipt must prove"
            conn.commit()
        _sid2, key2, digest2, _ = receipt_fixture(sess=sid, subject_id=subject)
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subject, key2, digest2, "m-f2", "m-f1"),
                    because="setup: second receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True, "the second receipt must prove"
            conn.commit()
        bad = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id, retracts_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,'cd-both',
                    'origin','m-f2',%s,%s)"""
        refuses(conn, bad, (uuid.uuid4(), sid, joe, subject, key, r1, r2),
                because="a receipt must not claim to both reverse and retract",
                role="carr_writer", expect_message="write_receipt_reverses_xor_retracts")
    check("req 6: a receipt cannot both reverse and retract",
          receipt_cannot_reverse_and_retract)

    def call_digest_is_subject_bound():
        """0220's core guarantee. The call digest now covers the receipt's OWN
        subject_type/subject_id, so a digest computed honestly for one subject
        must not be able to prove a receipt that names a different one --
        otherwise a digest is transferable between subjects and the conflict
        detector can be fed borrowed proof."""
        sid, key, digest_for_a, _subject_a = receipt_fixture()
        subject_b = uuid.uuid4()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject_b, key, digest_for_a, "m-borrowed", "origin"),
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
    check("req 6: a call digest computed for one subject cannot prove a receipt "
          "naming another", call_digest_is_subject_bound)

    def verb_mismatch_refuses_by_the_verb_guard():
        """0220, the readback's other half. The receipt must describe its OWN
        evidence: a receipt claiming 'log-activity' over a call that actually
        recorded a different verb is the 'retire-the-entire-drive' attack in
        miniature, and prove_write_receipt must refuse it outright rather than
        merely fail to prove it."""
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
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, "m-mislabelled", "origin"),
                    because="filing a mislabelled receipt is allowed; proving it is not")
        try:
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            conn.rollback()
            raise AssertionError(
                "a receipt proved against evidence recording a DIFFERENT verb")
        except psycopg.Error as exc:
            conn.rollback()
            assert "claims verb" in str(exc), (
                f"refused, but by a different guard: {str(exc).strip().splitlines()[0]}")
    check("req 6: a receipt cannot prove against evidence recording a different verb",
          verb_mismatch_refuses_by_the_verb_guard)

    def prior_state_must_have_existed():
        """0220 section (E). A FABRICATED prior names a state this subject
        NEVER REACHED, and nothing honest produces one -- it is refused, not
        merely left unproven, because the whole point of checking existence
        is to force an evader who wants to avoid conflicting with a real
        receipt to name a real prior, which is exactly what makes the
        conflict visible."""
        sid, key, digest, subject = receipt_fixture()
        refuses(conn, RECEIPT_INSERT,
                (uuid.uuid4(), sid, joe, subject, key, digest,
                 "m-invented", "a-state-nobody-produced"),
                because="a receipt must not build on a state its subject never reached",
                role="carr_writer", expect_message="never reached")
    check("req 6: a receipt cannot build on a state its subject never reached",
          prior_state_must_have_existed)

    def stale_but_real_prior_still_reduces_to_broken():
        """0220 section (E)'s whole point: the rule is EXISTENCE, never
        RECENCY. r4 repeats r2's transition (prior='X' again, after the head
        already moved on to 'Y') -- its prior is real, so the guard admits
        it; it is not the head, so the fold finds a gap; and it AGREES with
        r2 about material, so it is not a conflict. 'broken' is the only
        state left for it to produce, and this is the contract that would
        fail first if the guard were quietly upgraded to 'the prior must be
        the CURRENT head', which would make continuity_reducer's worst
        finding unreachable."""
        subject = uuid.uuid4()
        r1_sid, r1_key, r1_digest, _ = receipt_fixture(subject_id=subject)
        _r2sid, r2_key, r2_digest, _ = receipt_fixture(sess=r1_sid, subject_id=subject)
        _r3sid, r3_key, r3_digest, _ = receipt_fixture(sess=r1_sid, subject_id=subject)
        _r4sid, r4_key, r4_digest, _ = receipt_fixture(sess=r1_sid, subject_id=subject)
        r1, r2, r3, r4 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, r1_sid, joe, subject, r1_key, r1_digest, "X", "origin"),
                    because="setup: origin -> X")
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, r1_sid, joe, subject, r2_key, r2_digest, "Y", "X"),
                    because="setup: X -> Y")
        writer_runs(conn, RECEIPT_INSERT,
                    (r3, r1_sid, joe, subject, r3_key, r3_digest, "Z", "Y"),
                    because="setup: Y -> Z")
        writer_runs(conn, RECEIPT_INSERT,
                    (r4, r1_sid, joe, subject, r4_key, r4_digest, "Y", "X"),
                    because="setup: a STALE BUT REAL restatement of r1->r2's "
                            "transition, arriving after the head moved on to Z")
        with as_writer(conn), conn.cursor() as cur:
            for rid in (r1, r2, r3, r4):
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
                assert cur.fetchone()[0] is True, f"setup receipt {rid} must prove"
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
        """0220 section (E). 'origin' stays ALWAYS-ACCEPTABLE, deliberately,
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
        sid, key, digest, _ = receipt_fixture(subject_id=subject)
        writer_runs(conn, RECEIPT_INSERT,
                    (uuid.uuid4(), sid, joe, subject, key, digest, "m-seed", "origin"),
                    because="setup: a receipt already exists on this subject")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute(RECEIPT_INSERT,
                        (uuid.uuid4(), sid, joe, subject, key, digest, "m-again", "origin"))
            assert cur.rowcount == 1, \
                "an 'origin' prior was refused on a subject that already has receipts"
            conn.rollback()
    check("req 6: 'origin' is acceptable even after a subject has receipts",
          origin_remains_acceptable_after_receipts_exist)

    def honest_receipt_proves():
        """The whole point, driven as the writer: a receipt over evidence that
        session really wrote proves against a readback the DATABASE computes."""
        sid, key, digest, subject = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, "m-honest", "origin"),
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
        never wrote must end up with an UNPROVEN receipt rather than a proof."""
        sid, key, _digest, _subj = receipt_fixture()
        rid, subject = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key,
                     "a-digest-nobody-ever-wrote", "m-false", "origin"),
                    because="filing a receipt is allowed; proving a false one is not")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            assert cur.fetchone()[0] is False, \
                "a receipt claiming a digest it never wrote reported as PROVEN"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select is_proven from ops.write_receipt where id=%s", (rid,))
            assert cur.fetchone()[0] is False, "a false claim still reported is_proven"
    check("req 6: a receipt claiming what it did not write cannot prove",
          false_claim_does_not_prove)

    def readback_is_not_caller_supplied():
        """REJECTION 2, the structural half. If a caller can write the readback
        column, the readback proves nothing. carr_writer holds no UPDATE."""
        sid, key, digest, subject = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, "m-readback", "origin"),
                    because="setup")
        refuses(conn, "update ops.write_receipt set readback_digest=%s where id=%s",
                (digest, rid),
                because="a caller-supplied readback would make the proof circular",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 6: carr_writer cannot supply a readback itself", readback_is_not_caller_supplied)

    def receipt_needs_a_live_session():
        """REJECTION 1. The session is the APPLICATION session, held to the same
        standard as evidence: live, unexpired, unrevoked, matching actor and
        tenant. Not the database backend that happened to write it."""
        # Minted with a real future expiry and then waited out: 0208 refuses to
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
        """REJECTION 1 again, at readback time rather than insert time."""
        sid_a, key_a, digest_a, subject_a = receipt_fixture()
        sid_b = mint(conn, joe)
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid_b, joe, subject_a, key_a, digest_a, "m-cross-session", "origin"),
                    because="setup: a receipt on session B naming session A's evidence")
        try:
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            conn.rollback()
            raise AssertionError(
                "a receipt was proven against another session's evidence")
        except psycopg.Error as exc:
            conn.rollback()
            assert "different session" in str(exc).lower(), (
                f"refused, but by a different guard: {str(exc).strip().splitlines()[0]}")
    check("req 6: a receipt cannot be proven against another session's evidence",
          receipt_cannot_prove_another_sessions_evidence)

    def legacy_evidence_cannot_be_read_back():
        """A proof about a row 0208 says proves nothing is not a proof."""
        sid = mint(conn, joe)
        key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (key, joe, None),
                    because="setup: a legacy row")
        rid, subject = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, "d", "m-legacy", "origin"),
                    because="setup")
        try:
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            conn.rollback()
            raise AssertionError("a receipt was proven against LEGACY evidence")
        except psycopg.Error as exc:
            conn.rollback()
            assert "legacy evidence" in str(exc).lower(), (
                f"refused by a different guard: {str(exc).strip().splitlines()[0]}")
    check("req 6: legacy evidence cannot back a receipt", legacy_evidence_cannot_be_read_back)

    def reversal_must_be_exact():
        """REJECTION 3, corrected by 0220. 'This undoes that' is checked, not
        believed: an exact reversal is one whose MATERIAL claim equals the
        state its target built on -- never its call digest, which is proof of
        attachment and says nothing about subject state.

        Under the SPLIT schema an exact reversal must also actually PROVE: the
        old single-digest scheme made that impossible by construction (a
        reversal's claimed digest had to equal both the target's prior state
        AND the call readback, which can never be the same value). That defect
        is exactly what 0220 removes, so this contract now asserts BOTH
        halves: an inexact reversal is still refused, and an EXACT one is
        accepted AND proves.

        THE TARGET IS ALSO PROVEN, here, which the earlier draft of this
        contract did not do. Left permanently unproven, the target would sit
        on a subject with a real successor (the reversal) sharing its exact
        material as that successor's prior -- and 0220's global, unscoped
        acceptance-bar sweep would eventually have to retract the target
        using that SAME material, colliding with the reversal that already
        claims it. Proving both removes either row from ever being swept."""
        sid, key, digest, subject = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, "m-target", "origin"),
                    because="setup: the receipt to be reversed")
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
        refuses(conn, bad, (uuid.uuid4(), sid, joe, subject, key, "m-target", rid),
                because="a reversal that does not restore the prior state is not a reversal",
                role="carr_writer", expect_message="reversal is not exact")
        # And the exact one is accepted AND proves -- the headline fix 0220
        # exists for. A reversal's material equals the target's prior state
        # (now 'origin', a legal prior even though this subject already has
        # a receipt on it), and its call digest is computed for THIS call
        # and THIS subject, so nothing stops it proving the way any other
        # honest receipt does.
        rev_id = uuid.uuid4()
        _rev_sid, rev_key, rev_digest, _ = receipt_fixture(sess=sid, subject_id=subject)
        good = """insert into ops.write_receipt
            (id, application_session_id, actor_id, organization_tenant_id, verb,
             subject_type, subject_id, tool_call_idempotency_key, call_digest,
             material_digest, prior_digest, reverses_receipt_id)
            values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,
                    %s,'origin',%s,%s)"""
        writer_runs(conn, good,
                    (rev_id, sid, joe, subject, rev_key, rev_digest, "m-target", rid),
                    because="an exact reversal must be permitted, or the guard is "
                            "simply refusing everything")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (rev_id,))
            proved = cur.fetchone()[0]
            conn.commit()
        assert proved is True, (
            "an EXACT reversal did not prove -- the defect 0220 exists to remove "
            "is still present")
    check("req 6: a reversal must land exactly where its target began, and an "
          "exact one proves", reversal_must_be_exact)

    def conflicts_are_derived_not_declared():
        """REJECTION 3. Two receipts conflict when they build on the same prior
        state and produce different MATERIAL claims — evaluated, never
        asserted. 0220 moves this comparison from claimed_digest (a digest of
        the CALL) to material_digest (the claim about the SUBJECT).

        EVERY RECEIPT BELOW IS PROVEN. Not because ops.receipt_conflicts cares
        about is_proven (it does not check it at all), but because an
        unproven receipt sharing a subject with a real successor is exactly
        the shape 0220's global, unscoped acceptance-bar sweep would later
        retract using that successor's own prior value — and if this subject
        already has a receipt claiming that exact (prior, different material)
        pair, the sweep's retraction and that receipt would conflict for
        real, permanently. Proving everything removes every row here from
        ever being swept, so this contract's own fixture cannot become
        tomorrow's unreconcilable conflict.

        THE ONLY LEGAL PRIOR FOR A SUBJECT'S FIRST RECEIPT IS 'ORIGIN' (0220
        section E), so the shared prior below is 'origin' itself -- which is
        exactly the case section E carves out as always acceptable, even
        though it makes two receipts on one subject share it deliberately."""
        subject = uuid.uuid4()
        a_sid, a_key, a_digest, _ = receipt_fixture(subject_id=subject)
        _sid2, b_key, b_digest, _ = receipt_fixture(sess=a_sid, subject_id=subject)
        a, b = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (a, a_sid, joe, subject, a_key, a_digest, "material-a", "origin"),
                    because="setup")
        writer_runs(conn, RECEIPT_INSERT,
                    (b, a_sid, joe, subject, b_key, b_digest, "a-different-result", "origin"),
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
        s1_sid, s1_key, s1_digest, _ = receipt_fixture(subject_id=seq_subject)
        _s2sid, s2_key, s2_digest, _ = receipt_fixture(sess=s1_sid, subject_id=seq_subject)
        s1, s2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (s1, s1_sid, joe, seq_subject, s1_key, s1_digest, "state-1", "origin"),
                    because="setup: first write")
        writer_runs(conn, RECEIPT_INSERT,
                    (s2, s1_sid, joe, seq_subject, s2_key, s2_digest, "state-2", "state-1"),
                    because="setup: a second write built on the first")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (s1,))
            assert cur.fetchone()[0] is True
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
        _rsid, rev_key, rev_digest, _ = receipt_fixture(sess=a_sid, subject_id=subject)
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
        lone_sid, lone_key, lone_digest, _ = receipt_fixture(subject_id=lone)
        writer_runs(conn, RECEIPT_INSERT,
                    (uuid.uuid4(), lone_sid, joe, lone, lone_key, lone_digest,
                     "material-lone", "origin"),
                    because="setup")
        with conn.cursor() as cur:
            cur.execute("select count(*) from ops.receipt_conflicts('deal', %s)", (lone,))
            assert cur.fetchone()[0] == 0, \
                "a single receipt must not report as conflicting with itself"
    check("req 6: conflict is derived from causal history, not declared",
          conflicts_are_derived_not_declared)

    def receipts_are_frozen():
        sid, key, digest, subject = receipt_fixture()
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, subject, key, digest, "m-frozen", "origin"),
                    because="setup")
        refuses(conn, "delete from ops.write_receipt where id=%s", (rid,),
                because="a receipt that can be deleted is not a receipt",
                role="carr_writer", expect_message="permission denied",
                privilege_is_the_point=True)
    check("req 6: carr_writer cannot delete a receipt", receipts_are_frozen)

    # ------------------------- 0213: the reducer and Phase 4 acceptance ----
    def reducer_reports_the_worst_thing_it_finds():
        """A fold, not a flag. The state is derived from the causal chain every
        time it is asked, so it cannot drift from the evidence — and it reports
        the WORST thing present, because a reducer that reported the best would
        call a damaged chain healthy the moment one receipt in it was fine."""
        subject = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute("select state from ops.continuity_reducer('deal', %s)", (subject,))
            assert cur.fetchone()[0] == "empty", "no receipts must reduce to empty"

        sid, key, call_digest, _subj = receipt_fixture(subject_id=subject)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subject, key, call_digest, "produced-1", "origin"),
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
        assert head == "produced-1", \
            "the head must be the last MATERIAL claim, never the call digest"
    check("req 6: the reducer folds receipts into a state derived from the chain",
          reducer_reports_the_worst_thing_it_finds)

    def reducer_names_where_the_chain_broke():
        """A gap is not merely reported, it is located. 'Something is wrong with
        this subject' is not actionable; 'the chain broke at this receipt' is.

        THE BREAK IS A STALE-BUT-REAL PRIOR (0220 section E), not a
        fabricated one: a3 repeats the transition a1->a2 already made
        (prior='bm1' again, after the head moved on to 'bm2'), which the
        guard admits because 'bm1' really existed on this subject -- it is
        simply not the LATEST state. Every receipt here is proven, both
        because that is what an honest producer would do and because an
        unproven receipt sharing this subject with a real successor is
        exactly what 0220's global, unscoped acceptance-bar sweep would
        later collide with."""
        subject = uuid.uuid4()
        a1_sid, a1_key, a1_digest, _ = receipt_fixture(subject_id=subject)
        _a2sid, a2_key, a2_digest, _ = receipt_fixture(sess=a1_sid, subject_id=subject)
        _a3sid, a3_key, a3_digest, _ = receipt_fixture(sess=a1_sid, subject_id=subject)
        a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (a1, a1_sid, joe, subject, a1_key, a1_digest, "bm1", "origin"),
                    because="setup: first link")
        writer_runs(conn, RECEIPT_INSERT,
                    (a2, a1_sid, joe, subject, a2_key, a2_digest, "bm2", "bm1"),
                    because="setup: second link, continuous")
        writer_runs(conn, RECEIPT_INSERT,
                    (a3, a1_sid, joe, subject, a3_key, a3_digest, "bm2", "bm1"),
                    because="setup: a STALE BUT REAL restatement of a1's transition, "
                            "arriving after the head already moved on to bm2")
        with as_writer(conn), conn.cursor() as cur:
            for rid in (a1, a2, a3):
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
        successor is exactly what 0220's global sweep would later have to
        retract using that successor's own prior, colliding with it for
        real. The shared prior for r1/r2 is 'origin' (always legal, per
        section E, even though two receipts deliberately share it here); r3
        builds honestly on r1's own material, which no reversal ever
        excludes, so it stays real and non-conflicting on its own."""
        subject = uuid.uuid4()
        r1_sid, r1_key, r1_digest, _ = receipt_fixture(subject_id=subject)
        _r2sid, r2_key, r2_digest, _ = receipt_fixture(sess=r1_sid, subject_id=subject)
        _r3sid, r3_key, r3_digest, _ = receipt_fixture(sess=r1_sid, subject_id=subject)
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
                    (r1, r1_sid, joe, subject, r1_key, r1_digest, "first", "origin"),
                    because="setup: the state both branches build on")
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, r1_sid, joe, subject, r2_key, r2_digest, "branch-a", "first"),
                    because="setup")
        # The same prior state, a different result: a conflict. It is also a
        # break, because whichever of the two folds second did not build on
        # what the other produced. Both problems are present at once, which is
        # the whole point of the precedence assertion below.
        writer_runs(conn, RECEIPT_INSERT,
                    (r3, r1_sid, joe, subject, r3_key, r3_digest, "branch-b", "first"),
                    because="setup: divergence, and a gap as well")
        with as_writer(conn), conn.cursor() as cur:
            for rid in (r1, r2, r3):
                cur.execute("select ops.prove_write_receipt(%s)", (rid,))
                assert cur.fetchone()[0] is True, f"setup receipt {rid} must prove"
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select state from ops.continuity_reducer('deal', %s)", (subject,))
            assert cur.fetchone()[0] == "conflicted", \
                "a conflict must outrank a mere break; both are present here"
        # RECONCILE BEFORE LEAVING. Acceptance counts open conflicts across the
        # whole database, so a contract that manufactures one and walks away
        # blocks every later contract that asks whether acceptance is reachable.
        # Leaving the store as it was found is part of the contract, not tidying.
        # The reversal is proven too, for the same reason as everything above.
        # RE-QUERY EACH TIME rather than walking one stale list. Closing a
        # conflict is a write like any other, so the set of open conflicts can
        # change underneath a loop that decided its work up front.
        for _attempt in range(6):
            with conn.cursor() as cur:
                cur.execute("""select left_receipt, right_receipt
                                 from ops.receipt_conflicts('deal', %s)""", (subject,))
                pairs = cur.fetchall()
            if not pairs:
                break
            right = pairs[0][1]
            with conn.cursor() as cur:
                cur.execute("""select prior_digest, material_digest
                                 from ops.write_receipt where id=%s""", (right,))
                r_prior, r_material = cur.fetchone()
            _rsid, rev_key, rev_digest, _ = receipt_fixture(sess=r1_sid, subject_id=subject)
            rev_id = uuid.uuid4()
            writer_runs(conn, """insert into ops.write_receipt
                    (id, application_session_id, actor_id, organization_tenant_id, verb,
                     subject_type, subject_id, tool_call_idempotency_key, call_digest,
                     material_digest, prior_digest, reverses_receipt_id)
                  values (%s,%s,%s,'carr-internal','log-activity','deal',%s,%s,%s,%s,%s,%s)""",
                        (rev_id, r1_sid, joe, subject, rev_key, rev_digest,
                         r_prior, r_material, right),
                        because="reconcile the conflict this contract created")
            with as_writer(conn), conn.cursor() as cur:
                cur.execute("select ops.prove_write_receipt(%s)", (rev_id,))
                assert cur.fetchone()[0] is True
                conn.commit()
        else:
            raise AssertionError(
                "reconciliation never converged: reversing one side of a conflict "
                "kept producing another")
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
        sid, key, digest, subject = receipt_fixture()
        anchor = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (anchor, sid, joe, subject, key, digest, "m-anchor-refuse", "origin"),
                    because="setup: an anchor receipt, proven so this contract's own "
                            "refusal is unambiguously about the UNPROVEN receipt below")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (anchor,))
            assert cur.fetchone()[0] is True, "the anchor receipt must prove"
            conn.commit()
        rid_subject = uuid.uuid4()
        rid_key = str(uuid.uuid4())
        writer_runs(conn, TOOL_CALL_INSERT, (rid_key, joe, sid),
                    because="setup: the call the deliberately-unproven receipt names")
        rid = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (rid, sid, joe, rid_subject, rid_key, "d", "m-unproven", "origin"),
                    because="setup: a receipt left deliberately UNPROVEN")
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


    # ------------------------------------- 0214: Drive retirement ----------
    # The static preflight (ops/drive-retirement-readiness-gate.py) refuses to
    # close Phase 4 on inventory alone, in its own words: it "cannot resolve
    # immutable repoint receipts, recovery receipts, or Joe's authority
    # receipt", and it has no --evidence argument because caller JSON is not a
    # receipt. These contracts exercise the record layer that answer points at.
    def retirement_needs_proven_receipts():
        """Not merely present — PROVEN. An unproven receipt is a claim the
        database has already declined to confirm."""
        sid, key, digest, _subj = receipt_fixture()
        subj_a, subj_b = uuid.uuid4(), uuid.uuid4()
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subj_a, key, digest, "m-repoint", "origin"),
                    because="setup: repoint receipt, left unproven")
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subj_b, key, digest, "m-recovery", "origin"),
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
    check("req 7: retirement refuses an unproven REPOINT receipt",
          retirement_needs_proven_receipts)

    def retirement_needs_a_proven_recovery_receipt():
        """The mirror. Proving one half and asserting on a shared phrase let a
        mutation that deleted the other half survive."""
        subj_1 = uuid.uuid4()
        sid, key, digest, _subj = receipt_fixture(subject_id=subj_1)
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subj_1, key, digest, "m-repoint", "origin"),
                    because="setup: repoint receipt, proven below")
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, uuid.uuid4(), key, digest, "m-recovery", "origin"),
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
    check("req 7: retirement refuses an unproven RECOVERY receipt",
          retirement_needs_a_proven_recovery_receipt)

    def one_receipt_cannot_make_both_claims():
        """Repointing a reader and proving recovery still works are different
        assertions. Letting one receipt stand for both is how 'we checked'
        becomes 'we checked once, sort of'.

        THE RECEIPT MUST NAME THE DEPENDENCY (0220) before this contract can
        even reach the table-level distinct-receipts constraint it used to be
        about, so the dependency is created FIRST and the receipt is filed
        against it -- naming a 'deal' subject here would be refused earlier,
        by the WRONG guard, for the wrong reason.

        THE GUARD THAT ACTUALLY FIRES ALSO CHANGED. Passing the SAME receipt
        id for both roles trivially means they share one call AND one
        material claim, and 0220's new same-call check now fires -- inside
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
        sid, key, digest, _subj = receipt_fixture(subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key, digest, "m-repoint", "origin"),
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
    check("req 7: one receipt cannot serve as both the repoint and the recovery",
          one_receipt_cannot_make_both_claims)

    def retirement_receipts_must_name_the_dependency():
        """0220 (D). Each receipt must NAME the dependency being retired, or a
        reviewer could retire a dependency with receipts that had never heard
        of it -- 0214's two-receipt gate separated its two receipts by
        nothing but row ids until this migration."""
        subj_1, subj_2 = uuid.uuid4(), uuid.uuid4()
        sid, key1, digest1, _s1 = receipt_fixture(subject_id=subj_1)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r1, sid, joe, subj_1, key1, digest1, "m-g1", "origin"),
                    because="setup: repoint receipt, about a 'deal', not a dependency")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2 = receipt_fixture(sess=sid, subject_id=subj_2)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT,
                    (r2, sid, joe, subj_2, key2, digest2, "m-g2", "origin"),
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
    check("req 7: a retirement receipt must name the dependency being retired",
          retirement_receipts_must_name_the_dependency)

    def retirement_receipts_cannot_share_a_call():
        """0220 (D). Two receipts describing ONE call are one piece of
        evidence counted twice, even when they are two distinct rows.

        r2's prior is r1's OWN material, not 'origin' -- both are legal
        under the new prior-existence guard, but sharing 'origin' would make
        r1 and r2 conflict with each other permanently (same prior,
        different material), which has nothing to do with what this
        contract is testing."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key, digest, _subj = receipt_fixture(subject_type="drive_dependency", subject_id=dep)
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key, digest, "repointed-h", "origin"),
                    because="setup: repoint receipt")
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key, digest, "repointed-h-again", "repointed-h"),
                    because="setup: a SECOND receipt naming the SAME call")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "same call, two rows"),
                because="two receipts resting on ONE call are one claim counted twice",
                role="carr_writer", expect_message="rest on the same call")
    check("req 7: the two retirement receipts cannot rest on the same call",
          retirement_receipts_cannot_share_a_call)

    def retirement_receipts_cannot_share_material():
        """0220 (D). Different calls, but asserting the SAME material state,
        is still one piece of work counted twice -- repointing and recovering
        are two claims, not one claim made from two calls."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1 = receipt_fixture(subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, "repointed-i", "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2 = receipt_fixture(sess=sid, subject_type="drive_dependency",
                                                      subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, "repointed-i", "origin"),
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
    check("req 7: the two retirement receipts cannot assert the same material state",
          retirement_receipts_cannot_share_material)

    def recovery_must_build_on_the_repoint():
        """0220 (D). Recovery is only meaningful from the state the repoint
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
        sid, key1, digest1, _s1 = receipt_fixture(subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, "repointed-j", "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _bsid, bkey, bdigest, _bs = receipt_fixture(sess=sid, subject_type="drive_dependency",
                                                     subject_id=dep)
        bridge = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (bridge, sid, joe, dep, bkey, bdigest, "unrelated-state", "repointed-j"),
                    because="setup: a bridge receipt giving the recovery below a real, "
                            "unrelated state to rest on")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (bridge,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2 = receipt_fixture(sess=sid, subject_type="drive_dependency",
                                                      subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, "recovered-j", "unrelated-state"),
                    because="setup: recovery receipt that ignores the repoint")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r2,))
            assert cur.fetchone()[0] is True
            conn.commit()
        refuses(conn, RETIREMENT_INSERT,
                (uuid.uuid4(), dep, r1, r2, sid, joe, "recovery ignores the repoint"),
                because="a recovery must build on the state the repoint actually produced",
                role="carr_writer", expect_message="does not build on the repointed state")
    check("req 7: the recovery receipt must build on the repointed state",
          recovery_must_build_on_the_repoint)

    def readiness_is_computed_and_needs_authority():
        """A function over the rows, not a flag, and it will not read ready
        without an acceptance only the authority identity can create.

        THE TWO RECEIPTS MUST BE HONEST NOW (0220): each must name the
        dependency, rest on DIFFERENT calls, assert DIFFERENT material
        claims, and the recovery must build on what the repoint produced.
        Sharing one call/material (as this contract's setup did before 0220)
        is now refused before ever reaching drive_retirement_readiness."""
        with conn.cursor() as cur:
            cur.execute("""insert into ops.drive_dependency
                             (source_path, reference, classification, operational)
                           values (%s, '{{VAULT}}', 'vault-path', true) returning id""",
                        (f"contract/{uuid.uuid4()}.py:1",))
            dep = cur.fetchone()[0]
            conn.commit()
        sid, key1, digest1, _s1 = receipt_fixture(subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, "repointed", "origin"),
                    because="setup: the repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True, "the repoint receipt must prove"
            conn.commit()
        _sid2, key2, digest2, _s2 = receipt_fixture(sess=sid, subject_type="drive_dependency",
                                                      subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, "recovered", "repointed"),
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
        sid, key1, digest1, _s1 = receipt_fixture(subject_type="drive_dependency", subject_id=dep)
        r1 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r1, sid, joe, dep, key1, digest1, "repointed-k", "origin"),
                    because="setup: repoint receipt")
        with as_writer(conn), conn.cursor() as cur:
            cur.execute("select ops.prove_write_receipt(%s)", (r1,))
            assert cur.fetchone()[0] is True
            conn.commit()
        _sid2, key2, digest2, _s2 = receipt_fixture(sess=sid, subject_type="drive_dependency",
                                                      subject_id=dep)
        r2 = uuid.uuid4()
        writer_runs(conn, RECEIPT_INSERT_DEP,
                    (r2, sid, joe, dep, key2, digest2, "recovered-k", "repointed-k"),
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

    def empty_inventory_is_not_ready():
        """Nothing proven about nothing is not proof. A readiness function that
        returns true for an empty inventory would report a system with no Drive
        dependencies recorded as fully retired."""
        with conn.cursor() as cur:
            cur.execute("select ready, operational_total from ops.drive_retirement_readiness()")
            ready, total = cur.fetchone()
        if total == 0:
            assert ready is False, "an empty inventory reported READY"
        else:
            # The suite has recorded dependencies; assert the rule directly.
            cur = conn.cursor()
            cur.execute("""select (op.n > 0 and op.n = ret.n and auth.n > 0) from
                (select count(*) n from ops.drive_dependency where operational) op,
                (select count(*) n from ops.drive_retirement r
                   join ops.drive_dependency d on d.id=r.drive_dependency_id
                  where d.operational) ret,
                (select count(*) n from ops.phase4_acceptance) auth""")
            expected = cur.fetchone()[0]
            cur.close()
            assert ready == expected, \
                "readiness disagreed with the rule it claims to compute"
    check("req 7: an empty inventory is not ready", empty_inventory_is_not_ready)

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

    def phase4_surface_exists_and_is_gated():
        """THIS CONTRACT USED TO ASSERT THE OPPOSITE, and the reversal is the
        point rather than a loosening.

        Every slice before this one ran under an assertion that no reducer, no
        acceptance state and no completion claim existed anywhere. That was not
        bureaucracy: a system that can declare itself finished before it can
        prove anything will do exactly that, and the declaration is what
        everyone downstream trusts. 0213 introduces the surface deliberately,
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
