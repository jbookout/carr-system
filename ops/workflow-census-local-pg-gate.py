#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-PostgreSQL acceptance for the V5-F09 workflow census store
(migration 0708: ops.workflow_census_record, its write door
ops.record_workflow_census and its read door ops.read_workflow_census).

WHY THIS EXISTS. The node suite (mcp-server/test/workflow-census.test.mjs)
drives the verbs against a JS fake, and the Python suite
(ops/workflow-census-attestation-selftest.py) drives the verifier against chains
it builds itself. Neither can prove what the database does. This gate records
real rows through the real door and hands the real read-door answer to
lib/workflow_census_attestation.verify_census_chain.

WHO IT IS WHILE IT CHECKS. The writer and the reader here are NOT the
superuser the gate connects as. It creates two throwaway LOGIN roles inside its
own transaction -- one a member of carr_writer, one a member of carr_reader --
and becomes each with SET SESSION AUTHORIZATION, which changes session_user
exactly as logging in as that role would (and so what the door records as
db_session_principal, and what the chain guard compares against). A second
connection is not used because a role created in this rolled-back transaction
does not exist for any other connection. The superuser identity is used only
for the database-owner attacks, which are the point of those checks.

WHAT IT PROVES, each by a refusal or a verdict on real rows:
  1. The database's row hash equals the reader's on two pinned vectors and on
     real rows with unicode, astral characters and escapes.
  2. The door takes its principal from carr.acting_actor_slug only: none, or a
     value that is not an actor slug, is refused by name.
  3. The door refuses key reuse (another payload, or the same payload under
     another principal), a fractional number, a wrong shape and a payload over
     the 4 MiB cap; an idempotent replay returns the same row.
  4. The non-owner writer login cannot touch the table directly, and cannot
     forge identity through it.
  5. The chain guard refuses an OWNER insert that forges principal or login
     role, splices, back-dates, stamps the future, or forges a digest -- with
     every trigger on.
  6. All three triggers are ENABLE ALWAYS, the read door reports that, and a
     disable followed by a plain ENABLE (state 'O') reads as tampered.
  7. The reviewer's wholesale rewrite (disable the triggers, delete, re-insert
     a clean chain, re-enable ALWAYS) verifies on its own and reads as tampered
     against the anchor the Worker would have recorded -- and STAYS tampered
     after the next write: the door refuses to append to a head the anchor does
     not hold, and the anchor refuses a row linked to the forged head.
  8. Freshness is measured on the DATABASE clock: a genesis row 27 hours old by
     that clock is stale, and read with max_rows smaller than the chain the
     answer says truncated.
  9. The read door's guard function digests are the pins in the checked-in
     attestation config; a guard body replaced while its trigger stays ENABLE
     ALWAYS reads tampered, with or without a forged row, and stays tampered
     after the next write.
 10. A write that commits but never reaches the anchor (the crash window)
     reads anchor_gap; the door refuses any other write meanwhile; the writer's
     retry of the SAME key replays the row, advances the anchor and the chain
     attests again, and a later retry of it answers replayed.
 12. R3-C1, REPLAY LAUNDERING: the owner forges a row linked to the anchored
     head under key K (triggers off, then back ENABLE ALWAYS) and the writer
     calls the door with K; the door replays the forged row, the Worker
     registers nothing for a replay, the anchor refuses it
     (anchor_pending_missing_refused) and stays where it was, and the reader
     says anchor_gap -- while 10's genuine crash gap still recovers because
     its pending entry exists.
 13. R4-C1, A REPLACED WRITE DOOR that stores a forged payload: when it
     answers with the row it stored, the Worker's own verification (payload
     digest, row hash, seq, prev_hash, principal, time window) refuses it, so
     nothing is registered and the anchor does not move; when it lies and
     answers with the honest row, the anchor takes the honest hash and the
     reader says tampered. 1b checks the Worker's canonical JSON equals the
     database's and the reader's over a varied corpus.
 11. The re-anchor door runs only as carr_authority with a verified partner,
     refuses a moved or unneeded head, records a receipt with the rows the
     anchor never vouched for, and once applied the chain attests with the
     receipt named in the claim.

THE ANCHOR here is the Worker's own decision code: every advance and re-anchor
runs mcp-server/src/workflow-census-anchor.js's decideAnchorAdvance /
decideAnchorReanchor under node, over state this gate keeps as the Durable
Object would, so the strict-linkage rule is the deployed rule, not a copy.
"""

from __future__ import annotations

import copy
import functools
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gate_runtime_role import rollback_only_connection  # noqa: E402
from lib.workflow_census_attestation import (  # noqa: E402
    canonical_json, load_config, row_hash, sha256_hex, verify_census_chain)

WRITER = "workflow-census-gate"
WRITER_LOGIN = "workflow_census_gate_writer"
READER_LOGIN = "workflow_census_gate_reader"
AUTHORITY_LOGIN = "workflow_census_gate_authority"
ANCHOR_MODULE = REPO / "mcp-server" / "src" / "workflow-census-anchor.js"
VERB_MODULE = REPO / "mcp-server" / "src" / "workflow-census.js"
PINNED_GUARD_FUNCTIONS = json.loads(
    (REPO / "ops" / "config" / "workflow-census-attestation.v1.json").read_text(encoding="utf-8")
)["guard_function_sha256"]
MIGRATION = (REPO / "migrations" / "0708_workflow_census_store.sql").read_text(encoding="utf-8")
# Escapes, a BMP character above U+E000 and an astral one, keys whose code-point
# order differs from their UTF-16 order: every place the two canonical
# renderings could disagree, so the cross-language check below means something.
CENSUS: dict[str, Any] = {
    "schema_version": "control-plane-workflow-truth.v1",
    "rows": [{"workflow_key": "gate-flow", "workflow_version": 1, "state": "enabled_shadow_only",
              "note": "é ☃ 日本 \n\t\"q\" \\ \u0001   /"}],
    "summary": {"workflows": 1, "ｚ": 1, "𝄞": 2, "z": None, "big": 12345678901234},
}
FORGED = {"schema_version": "control-plane-workflow-truth.v1",
          "rows": [{"workflow_key": "forged-all-clear"}], "summary": {"workflows": 99}}
# The same two vectors ops/workflow-census-attestation-selftest.py pins.
VECTOR_ROW = {"seq": 7, "recorded_at": "2026-09-24T04:10:00.123456Z", "principal": "joe-local",
              "db_session_principal": "carr_writer", "prev_hash": "a" * 64,
              "payload_sha256": "b" * 64}
VECTORS = ((VECTOR_ROW, "d06373a7bf4840bfbc2fab8bae4caca4e2cd42cf289c116174460af02864186f"),
           ({**VECTOR_ROW, "seq": 1, "prev_hash": None},
            "c557fc0fa00e3c53b091055baa3eb469fc015e72f38e1b36a5e36ffae3f5be9d"))
GUARDS = ("workflow_census_record_append_only", "workflow_census_record_chain_guard",
          "workflow_census_record_no_truncate")


def fail(message: str) -> int:
    print(f"workflow-census-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def config(*principals: str, sessions: tuple[str, ...] = (WRITER_LOGIN,)) -> dict[str, Any]:
    return load_config({
        "schema_version": "workflow-census-attestation.v1",
        "writer_principals": list(principals or (WRITER,)),
        "writer_db_session_principals": list(sessions),
        "writer_cadence_seconds": 86400, "freshness_window_seconds": 93600,
        "max_chain_rows": 20000, "guard_function_sha256": PINNED_GUARD_FUNCTIONS})


_DECIDE = """
const m = await import(process.argv[1]);
const input = JSON.parse(process.argv[2]);
const now = "2026-09-24T00:00:00.000Z";
const out = input.kind === "advance"
  ? m.decideAnchorAdvance(input.stored, input.proposed, now, seq => input.history[String(seq)],
      key => input.pending[key])
  : input.kind === "pending"
    ? m.decidePendingRegister(input.stored, input.pending, input.proposed, now)
    : m.decideAnchorReanchor(input.stored, input.last, input.receipt, now);
process.stdout.write(JSON.stringify(out));
"""


# The Worker's own row verification and canonical JSON (workflow-census.js).
_VERIFY = """
const m = await import(process.argv[1]);
const input = JSON.parse(process.argv[2]);
const out = input.kind === "verify"
  ? await m.verifyInsertedCensusRow({ ...input.args, nowMs: Date.now() })
  : input.kind === "row_hash" ? await m.censusRowHash(input.row)
  : await Promise.all(input.corpus.map(async c => ({ text: JSON.stringify(c),
      canonical: m.canonicalCensusJson(c), sha: await m.censusPayloadSha256(c) })));
process.stdout.write(JSON.stringify(out));
"""


def worker_js(payload: dict[str, Any]) -> Any:
    proc = subprocess.run(["node", "--input-type=module", "-e", _VERIFY, VERB_MODULE.as_uri(),
                           json.dumps(payload)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"the verb module did not run under node: {proc.stderr[-300:]}")
    return json.loads(proc.stdout)


# A varied census corpus for the three-way canonical JSON parity check: key
# order (case, digits, empty, punctuation), unicode (combining marks, BMP above
# U+E000, astral, RTL, U+2028/9, DEL, NBSP, BOM), every JSON escape, the safe
# integer range, nulls, booleans, nesting, empty containers.
PARITY_CORPUS: list[Any] = [
    CENSUS,
    {"b": 1, "a": 2, "B": 3, "A": 4, "_": 5, "~": 6, "10": 7, "9": 8, "": 9, "aa": 10, "a b": 11, "a\tb": 12},
    {"é": "é", "e\u0301": "combining", "ｚ": "ﬀ", "𝄞": "𝄞🎉", "日本": "語", "עברית": "rtl",
     "\u2028": "\u2029", "\u007f": "del", "\u00a0": "nbsp", "\ufeff": "bom", "\uffff": "last-bmp"},
    {"s": "\"\\/\b\f\n\r\t\u0001\u001f", "k\"ey\\": "v"},
    {"zero": 0, "neg": -1, "max": 9007199254740991, "min": -9007199254740991, "big": 12345678901234,
     "thousand": 1000},
    {"n": None, "t": True, "f": False, "arr": [None, [], {}, [[1, [2]]], {"b": {"a": [{"d": 1, "c": 2}]}}],
     "empty": {}},
    [3, 1, 2, {"b": [], "a": None}],
    {"schema_version": "control-plane-workflow-truth.v1", "summary": {"workflows": 50},
     "rows": [{"workflow_key": f"flow-{i}", "workflow_version": i, "state": "enabled_shadow_only"}
              for i in range(50)]},
]


class Anchor:
    """The Durable Object's state, decided by the Worker's own module under node."""

    def __init__(self) -> None:
        self.stored: dict[str, Any] | None = None
        self.history: dict[str, str] = {}
        self.last: dict[str, Any] | None = None
        self.pending: dict[str, dict[str, Any]] = {}

    def _decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        proc = subprocess.run(["node", "--input-type=module", "-e", _DECIDE, ANCHOR_MODULE.as_uri(),
                               json.dumps(payload)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"the anchor module did not run under node: {proc.stderr[-300:]}")
        return json.loads(proc.stdout)

    def register(self, seq: int, row_hash: str, prev_hash: str | None, key: str) -> dict[str, Any]:
        """The Worker's pre-commit step for a FRESH insert (the object's /pending route)."""
        verdict = self._decide({"kind": "pending", "stored": self.stored, "pending": self.pending,
                                "proposed": {"seq": seq, "row_hash": row_hash, "prev_hash": prev_hash,
                                             "idempotency_key": key}})
        if verdict.get("write"):
            for dead in verdict["prune"]:
                self.pending.pop(dead, None)
            self.pending[verdict["key"]] = verdict["entry"]
        return verdict["response"]

    def advance(self, seq: int, row_hash: str, prev_hash: str | None,
                key: str | None = None) -> dict[str, Any]:
        proposed: dict[str, Any] = {"seq": seq, "row_hash": row_hash, "prev_hash": prev_hash}
        if key is not None:
            proposed["idempotency_key"] = key
        verdict = self._decide({"kind": "advance", "stored": self.stored, "history": self.history,
                                "pending": self.pending, "proposed": proposed})
        if verdict.get("write"):
            self.stored = verdict["head"]
            self.history[str(seq)] = row_hash
            self.pending = {k: e for k, e in self.pending.items()
                            if k != verdict["clear"] and e.get("seq", 0) > seq}
        return verdict["response"]

    def reanchor(self, receipt: dict[str, Any]) -> dict[str, Any]:
        verdict = self._decide({"kind": "reanchor", "stored": self.stored, "last": self.last,
                                "receipt": receipt})
        if verdict.get("write"):
            self.stored, self.last = verdict["head"], verdict["record"]
            self.pending = {}
            self.history = {} if verdict["head"] is None else {
                str(verdict["head"]["seq"]): verdict["head"]["row_hash"]}
        return verdict["response"]

    def view(self) -> dict[str, Any]:
        if self.stored is None:
            return {"state": "absent", "last_reanchor": self.last}
        return {"state": "present", **self.stored, "last_reanchor": self.last}

    def head_params(self) -> tuple[int | None, str | None]:
        return (None, None) if self.stored is None else (self.stored["seq"], self.stored["row_hash"])

    def snapshot(self) -> tuple[Any, ...]:
        return copy.deepcopy((self.stored, self.history, self.last, self.pending))

    def restore(self, state: tuple[Any, ...]) -> None:
        self.stored, self.history, self.last, self.pending = copy.deepcopy(state)


ANCHOR = Anchor()


def become(cur: psycopg.Cursor[Any], login: str | None) -> None:
    """Take a login role's session identity (None: back to the superuser)."""
    cur.execute("reset session authorization" if login is None
                else f"set session authorization {login}")
    who = cur.execute("select session_user::text, current_user::text").fetchone()
    if who is None:
        raise RuntimeError("session identity was not returned")
    expected = login or who[0]
    if who[0] != expected or who[1] != expected:
        raise RuntimeError(f"session identity is {who}, expected {expected}")


ACTING: dict[str, str | None] = {"slug": None}


def actor(cur: psycopg.Cursor[Any], slug: str | None) -> None:
    """The Worker sets the acting actor from the server-derived actor; it
    therefore knows that slug itself (ACTING), without reading it back."""
    ACTING["slug"] = slug
    cur.execute("select set_config('carr.acting_actor_slug', %s, true)", (slug or "",))


def record(cur: psycopg.Cursor[Any], payload: Any = None, key: str | None = None, *,
           crash: bool = False,
           door: Callable[[tuple[Any, ...]], tuple[Any, ...]] | None = None) -> tuple[Any, ...]:
    """The Worker's write path: read the anchor, call the door with its head,
    register a FRESH insert as the anchor's pending head under its key (before
    commit; a replay registers nothing), then (unless the Worker "crashes"
    between commit and advance) advance the anchor to the committed row under
    the same key, and refuse by name if it will not advance. A fresh insert is
    first VERIFIED by the Worker's own code (verifyInsertedCensusRow) against
    the census sent, the acting slug, the session principal and the anchored
    head; `door` lets a scenario stand in for a lying door's answer."""
    anchor_seq, anchor_hash = ANCHOR.head_params()
    key = key or str(uuid.uuid4())
    census = CENSUS if payload is None else payload
    session = cur.execute("select session_user::text").fetchone()
    row = cur.execute(
        "select seq, recorded_at, principal, row_hash, prev_hash, payload_sha256, replayed "
        "from ops.record_workflow_census(%s, %s, %s, %s)",
        (Jsonb(census), key, anchor_seq, anchor_hash),
    ).fetchone()
    if row is None:
        raise RuntimeError("record_workflow_census returned no row")
    if door is not None:
        row = door(row)
    if row[6] is not True:
        verified = worker_js({"kind": "verify", "args": {
            "row": {"seq": row[0], "recorded_at": row[1], "principal": row[2], "row_hash": row[3],
                    "prev_hash": row[4], "payload_sha256": row[5]},
            "census": census, "principal": ACTING["slug"],
            "dbSessionPrincipal": session[0] if session else None,
            "anchored": {"seq": anchor_seq, "row_hash": anchor_hash}}})
        if verified.get("ok") is not True:
            raise RuntimeError(f"workflow_census_row_unverified: {verified}")
        pending = ANCHOR.register(row[0], row[3], row[4], key)
        if pending.get("ok") is not True:
            raise RuntimeError(f"workflow_census_anchor_pending_refused: {pending}")
    if not crash:
        answer = ANCHOR.advance(row[0], row[3], row[4], key)
        if answer.get("ok") is not True:
            raise RuntimeError(f"workflow_census_anchor_not_advanced: {answer}")
    return row


def read(cur: psycopg.Cursor[Any], max_rows: int = 20000) -> dict[str, Any]:
    row = cur.execute("select ops.read_workflow_census(%s)", (max_rows,)).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RuntimeError("read_workflow_census returned no object")
    return row[0]


def served(answer: dict[str, Any], anchor: dict[str, Any] | None = None) -> dict[str, Any]:
    """The verb's envelope around the read door's answer. The anchor is what the
    Worker's Durable Object holds (ANCHOR), unless a test passes another."""
    out = copy.deepcopy(answer)
    out["ok"] = True
    out["anchor"] = anchor if anchor is not None else ANCHOR.view()
    return out


def self_anchored(answer: dict[str, Any]) -> dict[str, Any]:
    """An answer served beside an anchor at its own head (for checks about the
    chain alone, such as the owner-written stale genesis)."""
    head = answer["chain"][-1] if answer["chain"] else None
    return served(answer, {"state": "present", "seq": head["seq"], "row_hash": head["row_hash"],
                           "anchored_at": head["recorded_at"]} if head else {"state": "absent"})


def door_source() -> str:
    """The migration's own CREATE OR REPLACE for the write door, to restore it."""
    start = MIGRATION.index("create or replace function ops.record_workflow_census(")
    end = MIGRATION.index("$$;", MIGRATION.index("as $$", start) + 5) + 3
    return MIGRATION[start:end]


def guard_function_source(name: str) -> str:
    """The migration's own CREATE OR REPLACE for one guard function, to restore it."""
    start = MIGRATION.index(f"create or replace function ops.{name}()")
    end = MIGRATION.index("$$;", MIGRATION.index("as $$", start) + 5) + 3
    return MIGRATION[start:end]


def verdict_of(cur: psycopg.Cursor[Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return verify_census_chain(served(read(cur)), cfg or config())


def reanchor(cur: psycopg.Cursor[Any], accept: tuple[int, str] | None, reason: str,
             key: str | None = None) -> tuple[Any, ...]:
    anchor_seq, anchor_hash = ANCHOR.head_params()
    row = cur.execute(
        "select receipt_id::text, recorded_at, actor, verified_partner, reason, old_seq, old_row_hash, "
        "new_seq, new_row_hash, rows_reattested, replayed "
        "from ops.reanchor_workflow_census(%s, %s, %s, %s, %s, %s)",
        (anchor_seq, anchor_hash, accept[0] if accept else None, accept[1] if accept else None,
         reason, key or str(uuid.uuid4()))).fetchone()
    if row is None:
        raise RuntimeError("reanchor_workflow_census returned no row")
    return row


def expect_refusal(cur: psycopg.Cursor[Any], fn: Callable[[], object],
                   errors: tuple[type[BaseException], ...], expected: str, label: str) -> None:
    cur.execute(f"savepoint {label}")
    try:
        fn()
    except errors as exc:
        cur.execute(f"rollback to savepoint {label}")
        if expected not in str(exc):
            raise RuntimeError(f"{label} refused with the wrong reason: {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {label}")
    raise RuntimeError(f"{label} was accepted; expected a refusal")


def owner_insert(cur: psycopg.Cursor[Any], seq: int, prev: str | None, at_sql: str, *,
                 principal: str | None = None, session: str | None = None,
                 payload: Any = None, payload_sha: str | None = None,
                 forged_row_hash: str | None = None, key: str | None = None) -> object:
    """An owner-level INSERT that skips the door, hashed genuinely unless told not to.
    principal/session default to the session's own (what the guard demands)."""
    return cur.execute(
        f"""with v as (
              select %(seq)s::bigint as seq, ({at_sql})::timestamptz as at,
                     coalesce(%(p)s, current_setting('carr.acting_actor_slug', true)) as p,
                     coalesce(%(s)s, session_user::text) as s, %(prev)s::text as prev,
                     %(payload)s::jsonb as payload)
            insert into ops.workflow_census_record
              (seq, recorded_at, principal, db_session_principal, prev_hash,
               payload_sha256, row_hash, payload, idempotency_key)
            select v.seq, v.at, v.p, v.s, v.prev,
                   coalesce(%(ps)s, ops.workflow_census_payload_sha256(v.payload)),
                   coalesce(%(rh)s, ops.workflow_census_row_hash(v.seq, v.at, v.p, v.s, v.prev,
                     coalesce(%(ps)s, ops.workflow_census_payload_sha256(v.payload)))),
                   v.payload, %(key)s
              from v""",
        {"seq": seq, "prev": prev, "p": principal, "s": session,
         "payload": Jsonb(FORGED if payload is None else payload),
         "ps": payload_sha, "rh": forged_row_hash, "key": key or str(uuid.uuid4())})


def head(cur: psycopg.Cursor[Any]) -> tuple[int, str]:
    row = cur.execute("select seq, row_hash from ops.workflow_census_record "
                      "order by seq desc limit 1").fetchone()
    if row is None:
        raise RuntimeError("no head row")
    return row[0], row[1]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            if not cur.execute("select rolsuper from pg_roles where rolname = session_user"
                               ).fetchone()[0]:
                return fail("the gate needs a superuser session to take login identities")
            prior = cur.execute("select count(*) from ops.workflow_census_record").fetchone()[0]
            if prior:
                return fail(f"the disposable database already holds {prior} census rows")
            cur.execute(f"create role {WRITER_LOGIN} login in role carr_writer")
            cur.execute(f"create role {READER_LOGIN} login in role carr_reader")
            cur.execute(f"create role {AUTHORITY_LOGIN} login in role carr_authority")

            # 1. Pinned vectors: the database's rule is the reader's rule.
            for vector, digest in VECTORS:
                got = cur.execute(
                    "select ops.workflow_census_row_hash(%s, %s::timestamptz, %s, %s, %s, %s)",
                    (vector["seq"], vector["recorded_at"], vector["principal"],
                     vector["db_session_principal"], vector["prev_hash"],
                     vector["payload_sha256"])).fetchone()[0]
                worker = worker_js({"kind": "row_hash", "row": vector})
                if got != digest or row_hash(vector) != digest or worker != digest:
                    raise RuntimeError(f"row-hash vector disagrees: db {got}, worker {worker}, pinned {digest}")

            # 1b. Canonical JSON parity, three ways: the Worker's JavaScript (which
            # now hashes the census itself, R4-C1), the database's
            # ops.scac_canonical_json over the text the Worker sends, and the
            # Python reader's, over a varied corpus.
            rendered = worker_js({"kind": "parity", "corpus": PARITY_CORPUS})
            for value, js in zip(PARITY_CORPUS, rendered, strict=True):
                db_canonical, db_sha = cur.execute(
                    "select ops.scac_canonical_json(%s::jsonb), "
                    "encode(public.digest(convert_to(ops.scac_canonical_json(%s::jsonb), 'UTF8'), 'sha256'), 'hex')",
                    (js["text"], js["text"])).fetchone()
                py_canonical = canonical_json(value)
                if not (js["canonical"] == db_canonical == py_canonical
                        and js["sha"] == db_sha == sha256_hex(py_canonical)):
                    raise RuntimeError(f"canonical JSON disagrees: js {js['canonical']!r} / db {db_canonical!r} "
                                       f"/ py {py_canonical!r}")
                if isinstance(value, dict) and value.get("schema_version"):
                    door_sha = cur.execute("select ops.workflow_census_payload_sha256(%s::jsonb)",
                                           (js["text"],)).fetchone()[0]
                    if door_sha != js["sha"]:
                        raise RuntimeError(f"the door's payload digest disagrees with the Worker's: {door_sha}")

            # 6a. All three guards ENABLE ALWAYS.
            states = dict(cur.execute(
                "select tgname::text, tgenabled::text from pg_trigger "
                "where tgrelid = 'ops.workflow_census_record'::regclass and not tgisinternal"
            ).fetchall())
            if states != {name: "A" for name in GUARDS}:
                raise RuntimeError(f"guards are not exactly the three ENABLE ALWAYS triggers: {states}")

            # 5a. An empty store admits only a true genesis, even from the owner.
            actor(cur, WRITER)
            expect_refusal(cur, lambda: owner_insert(cur, 5, "0" * 64, "clock_timestamp()"),
                           (psycopg.errors.RaiseException,), "workflow_census_chain_splice_refused",
                           "owner_orphan_genesis")

            # 8a. Freshness on the DATABASE clock: a genesis 27h old by that clock.
            cur.execute("savepoint stale_genesis")
            owner_insert(cur, 1, None, "clock_timestamp() - interval '27 hours'", payload=CENSUS)
            owner = cur.execute("select session_user::text").fetchone()[0]
            verdict = verify_census_chain(self_anchored(read(cur)), config(sessions=(owner,)))
            if verdict.get("reason") != "stale" or not 27 * 3600 <= verdict.get("age_seconds", 0) < 27 * 3600 + 600:
                raise RuntimeError(f"a genesis 27h old by the database clock was not stale: {verdict}")
            cur.execute("rollback to savepoint stale_genesis")

            # 2. As the writer LOGIN: no actor setting, or a non-slug, is refused.
            become(cur, WRITER_LOGIN)
            actor(cur, None)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_principal_unavailable", "no_principal")
            actor(cur, "Not A Slug!")
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_principal_unavailable", "bad_principal")

            # 3. The Worker shape: the actor setting, then the door.
            actor(cur, WRITER)
            first = record(cur)
            key = str(uuid.uuid4())
            second = record(cur, key=key)
            replay = record(cur, key=key)
            if first[0] != 1 or second[0] != 2 or first[4] is not None or second[4] != first[3]:
                raise RuntimeError(f"chain fields are not seq 1..2 linked by prev_hash: {first} {second}")
            if first[2] != WRITER or replay[6] is not True or replay[3] != second[3]:
                raise RuntimeError(f"principal or idempotent replay misbehaved: {first} {replay}")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "rows": []}, key=key),
                           (psycopg.errors.RaiseException,), "workflow_census_key_reuse", "key_reuse")
            actor(cur, "another-writer")
            expect_refusal(cur, lambda: record(cur, key=key), (psycopg.errors.RaiseException,),
                           "workflow_census_key_reuse", "key_reuse_other_principal")
            actor(cur, WRITER)
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "summary": {"ratio": 0.5}}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_fraction_refused",
                           "fraction")
            expect_refusal(cur, lambda: record(cur, payload={"schema_version": "other.v1"}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_shape_refused",
                           "shape")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "schema_version": "other.v1"}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_shape_refused",
                           "schema_only")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "rows": [{"blob": "x" * 4_200_000}]}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_too_large",
                           "too_large")

            # 4. The writer login has no table privilege, so it cannot forge through it.
            for label, statement in (
                ("select", "select count(*) from ops.workflow_census_record"),
                ("update", "update ops.workflow_census_record set principal = 'x'"),
                ("delete", "delete from ops.workflow_census_record"),
            ):
                expect_refusal(cur, functools.partial(cur.execute, statement),
                               (psycopg.errors.InsufficientPrivilege,), "permission denied",
                               f"writer_direct_{label}")
            expect_refusal(cur, lambda: owner_insert(cur, 3, second[3], "clock_timestamp()",
                                                     principal="joe-local", session="carr_writer"),
                           (psycopg.errors.InsufficientPrivilege,), "permission denied",
                           "writer_forged_insert")

            # 1b + reading as the reader LOGIN: the database's chain verifies here.
            become(cur, READER_LOGIN)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.InsufficientPrivilege,),
                           "permission denied", "reader_cannot_write")
            expect_refusal(cur, lambda: cur.execute("select count(*) from ops.workflow_census_record"),
                           (psycopg.errors.InsufficientPrivilege,), "permission denied",
                           "reader_direct_select")
            answer = read(cur)
            truncated = read(cur, 1)
            become(cur, None)
            if answer.get("guards") != {name: "A" for name in GUARDS}:
                raise RuntimeError(f"the read door did not report the guards: {answer.get('guards')}")
            # 9a. The real guard functions are the ones the checked-in config pins.
            if answer.get("guard_functions") != PINNED_GUARD_FUNCTIONS:
                raise RuntimeError("the read door's guard function digests are not the pinned ones: "
                                   f"{answer.get('guard_functions')}")
            if truncated.get("truncated") is not True or len(truncated.get("chain", [])) != 1:
                raise RuntimeError(f"max_rows 1 over a 2-row chain did not say truncated: {truncated}")
            if verify_census_chain(served(truncated), config()).get("detail") != "chain_truncated":
                raise RuntimeError("the reader accepted a truncated chain")
            verdict = verify_census_chain(served(answer), config())
            if verdict.get("available") is not True:
                raise RuntimeError(f"the reader could not recompute the database's chain: {verdict}")
            if verdict["attestation"]["seq"] != 2 or verdict["census"] != CENSUS:
                raise RuntimeError(f"the reader attested the wrong row: {verdict['attestation']}")
            if any(row["db_session_principal"] != WRITER_LOGIN for row in answer["chain"]):
                raise RuntimeError("db_session_principal is not the writer's login role")
            if verify_census_chain(served(answer), config("someone-else")).get("reason") != "unknown_writer":
                raise RuntimeError("a real row from an unlisted writer was not refused as unknown_writer")

            # 5. OWNER attacks with every trigger on. The owner may set the actor
            # setting; it still cannot write a row under any identity but its own.
            actor(cur, "joe-local")
            seq, prev = head(cur)
            for label, kwargs, expected in (
                ("owner_forged_principal", {"principal": "someone-else"}, "workflow_census_principal_forged"),
                ("owner_forged_session", {"session": "carr_writer"}, "workflow_census_session_principal_forged"),
                ("owner_forged_both", {"principal": "someone-else", "session": WRITER_LOGIN},
                 "workflow_census_session_principal_forged"),
            ):
                expect_refusal(cur, functools.partial(owner_insert, cur, seq + 1, prev,
                                                      "clock_timestamp()",
                                                      principal=kwargs.get("principal"),
                                                      session=kwargs.get("session")),
                               (psycopg.errors.RaiseException,), expected, label)
            owner_cases: list[tuple[str, tuple[int, str | None, str], dict[str, Any], str]] = [
                ("owner_seq_gap", (seq + 3, prev, "clock_timestamp()"), {}, "workflow_census_chain_splice_refused"),
                ("owner_wrong_prev", (seq + 1, "0" * 64, "clock_timestamp()"), {}, "workflow_census_chain_splice_refused"),
                ("owner_backdated", (seq + 1, prev, "'2020-01-01T00:00:00Z'"), {}, "workflow_census_time_regression_refused"),
                ("owner_future", (seq + 1, prev, "clock_timestamp() + interval '1 hour'"), {}, "workflow_census_future_time_refused"),
                ("owner_forged_payload_digest", (seq + 1, prev, "clock_timestamp()"), {"payload_sha": "a" * 64},
                 "workflow_census_payload_digest_mismatch"),
                ("owner_forged_row_hash", (seq + 1, prev, "clock_timestamp()"), {"forged_row_hash": "b" * 64},
                 "workflow_census_row_hash_mismatch"),
            ]
            for label, args, extra, expected in owner_cases:
                expect_refusal(cur, functools.partial(owner_insert, cur, *args, **extra),
                               (psycopg.errors.RaiseException,), expected, label)
            # The one owner row the guard admits carries the owner's own login role,
            # which no writer list names -- anywhere in the chain it fails the read.
            cur.execute("savepoint owner_append")
            owner_insert(cur, seq + 1, prev, "clock_timestamp()")
            appended = read(cur)
            owner_verdict = verify_census_chain(self_anchored(appended), config(WRITER, "joe-local"))
            if (owner_verdict.get("detail") != "db_session_principal_not_listed"
                    or owner_verdict.get("at_seq") != seq + 1):
                raise RuntimeError("an owner-appended row was not refused as an unlisted login role")
            if verify_census_chain(served(appended), config(WRITER, "joe-local")).get("reason") != "anchor_gap":
                raise RuntimeError("an owner-appended row the anchor never took is not an anchor gap")
            cur.execute("rollback to savepoint owner_append")
            expect_refusal(cur, lambda: cur.execute(
                "update ops.workflow_census_record set payload = '{}'::jsonb where seq = 1"),
                (psycopg.errors.RaiseException,), "append-only", "owner_edit_refused")
            expect_refusal(cur, lambda: cur.execute(
                "delete from ops.workflow_census_record where seq = 2"),
                (psycopg.errors.RaiseException,), "append-only", "owner_delete_refused")
            cur.execute("set local session_replication_role = replica")
            expect_refusal(cur, lambda: cur.execute(
                "delete from ops.workflow_census_record where seq = 2"),
                (psycopg.errors.RaiseException,), "append-only", "owner_replica_delete_refused")
            cur.execute("set local session_replication_role = origin")

            # 6b. Disable, then a plain ENABLE: state 'O', which the reader refuses.
            genuine = served(answer)
            cur.execute("savepoint guard_flip")
            cur.execute("alter table ops.workflow_census_record disable trigger workflow_census_record_chain_guard")
            cur.execute("alter table ops.workflow_census_record enable trigger workflow_census_record_chain_guard")
            flipped = verify_census_chain(served(read(cur)), config())
            if flipped.get("reason") != "tampered" or flipped.get("detail") != "guard_not_enforced":
                raise RuntimeError(f"a guard re-enabled as an ordinary trigger was not refused: {flipped}")
            cur.execute("rollback to savepoint guard_flip")

            # 7. The wholesale rewrite: triggers off, history deleted, a clean forged
            # chain written under the listed identities, triggers back ENABLE ALWAYS.
            cur.execute("savepoint rewrite")
            cur.execute("alter table ops.workflow_census_record disable trigger user")
            cur.execute("delete from ops.workflow_census_record")
            owner_insert(cur, 1, None, "clock_timestamp() - interval '1 hour'",
                         principal=WRITER, session=WRITER_LOGIN)
            for name in GUARDS:
                cur.execute(f"alter table ops.workflow_census_record enable always trigger {name}")
            rewritten = read(cur)
            alone = verify_census_chain(self_anchored(rewritten), config())
            if alone.get("available") is not True:
                raise RuntimeError(f"control failed: the rewritten chain should verify on its own: {alone}")
            against_anchor = verify_census_chain(served(rewritten, genuine["anchor"]), config())
            if against_anchor.get("reason") != "tampered":
                raise RuntimeError(f"a wholesale rewrite was not refused against the anchor: {against_anchor}")
            cur.execute("rollback to savepoint rewrite")

            # 7b. REWRITE, THEN WRITE: the round-2 laundering path. Same-length
            # rewrite (2 forged rows), guards back ALWAYS, then the Worker writes.
            saved = ANCHOR.snapshot()
            cur.execute("savepoint rewrite_then_write")
            cur.execute("alter table ops.workflow_census_record disable trigger user")
            cur.execute("delete from ops.workflow_census_record")
            owner_insert(cur, 1, None, "clock_timestamp() - interval '2 hours'",
                         principal=WRITER, session=WRITER_LOGIN)
            forged_head = head(cur)
            owner_insert(cur, 2, forged_head[1], "clock_timestamp() - interval '1 hour'",
                         principal=WRITER, session=WRITER_LOGIN)
            forged_head = head(cur)
            for name in GUARDS:
                cur.execute(f"alter table ops.workflow_census_record enable always trigger {name}")
            before = verdict_of(cur)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_tampered", "rewrite_then_write_door")
            become(cur, None)
            after = verdict_of(cur)
            # Even a row the owner links to the forged head cannot move the anchor.
            actor(cur, WRITER)
            cur.execute("alter table ops.workflow_census_record disable trigger user")
            owner_insert(cur, 3, forged_head[1], "clock_timestamp()", principal=WRITER, session=WRITER_LOGIN)
            for name in GUARDS:
                cur.execute(f"alter table ops.workflow_census_record enable always trigger {name}")
            forged3 = head(cur)
            carried = ANCHOR.advance(forged3[0], forged3[1], forged_head[1])
            grown = verdict_of(cur)
            cur.execute("rollback to savepoint rewrite_then_write")
            ANCHOR.restore(saved)
            if (before.get("reason"), after.get("reason"), grown.get("reason")) != ("tampered",) * 3:
                raise RuntimeError("a same-length rewrite did not stay tampered through the next write: "
                                   f"{before} / {after} / {grown}")
            if carried.get("error") != "anchor_link_refused":
                raise RuntimeError(f"the anchor took a row linked to a forged head: {carried}")

            # 9b. GUARD REPLACED, THEN WRITE. The chain guard's body is replaced
            # (its trigger stays ENABLE ALWAYS) and nothing else changes; then an
            # ordinary write, which the door and the anchor both accept.
            saved = ANCHOR.snapshot()
            cur.execute("savepoint guard_replaced")
            cur.execute("create or replace function ops.workflow_census_chain_guard() returns trigger "
                        "language plpgsql as $$ begin return new; end $$")
            replaced_only = verdict_of(cur)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            record(cur)
            become(cur, None)
            replaced_then_write = verdict_of(cur)
            # The reviewer's probe H: with the permissive guard, a forged row is
            # appended linked to the real head; the next write is refused.
            actor(cur, WRITER)
            real_head = head(cur)
            owner_insert(cur, real_head[0] + 1, real_head[1], "clock_timestamp()",
                         principal=WRITER, session=WRITER_LOGIN)
            forged_on_top = verdict_of(cur)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_anchor_gap", "guard_replaced_forged_row_door")
            become(cur, None)
            still = verdict_of(cur)
            # Restoring the guard's source clears the digest, not the forged row.
            cur.execute(guard_function_source("workflow_census_chain_guard"))
            restored = verdict_of(cur)
            cur.execute("rollback to savepoint guard_replaced")
            ANCHOR.restore(saved)
            for label, got in (("replaced", replaced_only), ("replaced then written", replaced_then_write),
                               ("forged row on top", forged_on_top), ("after a refused write", still)):
                if got.get("reason") != "tampered" or got.get("detail") != "guard_function_replaced":
                    raise RuntimeError(f"a replaced guard ({label}) did not read tampered: {got}")
            if restored.get("reason") != "anchor_gap":
                raise RuntimeError(f"a forged row linked to the anchored head, guard restored, is not an "
                                   f"anchor gap: {restored}")

            # 10. THE CRASH WINDOW: committed, never anchored; retry the SAME key.
            saved = ANCHOR.snapshot()
            cur.execute("savepoint crash_gap")
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            crash_key = str(uuid.uuid4())
            crashed = record(cur, key=crash_key, crash=True)
            become(cur, None)
            gap = verdict_of(cur)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_anchor_gap", "crash_gap_other_key")
            become(cur, None)
            gap_after_other = verdict_of(cur)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            retried = record(cur, key=crash_key)
            late = ANCHOR.advance(retried[0], retried[3], retried[4])
            following = record(cur)
            become(cur, None)
            recovered = verdict_of(cur)
            cur.execute("rollback to savepoint crash_gap")
            ANCHOR.restore(saved)
            if (gap.get("reason"), gap.get("detail")) != ("anchor_gap", "chain_one_linked_row_ahead_of_anchor"):
                raise RuntimeError(f"a committed-but-unanchored row is not an anchor gap: {gap}")
            if gap_after_other.get("reason") != "anchor_gap":
                raise RuntimeError(f"the gap did not persist across a refused write: {gap_after_other}")
            if retried[6] is not True or retried[3] != crashed[3]:
                raise RuntimeError(f"the same-key retry did not replay the crashed row: {retried}")
            if late.get("state") != "replayed" or following[0] != crashed[0] + 1:
                raise RuntimeError(f"a late retry was not replayed, or the next write did not follow: {late}")
            if recovered.get("available") is not True or recovered["attestation"]["seq"] != following[0]:
                raise RuntimeError(f"the chain did not attest after the retry: {recovered}")

            # 12. R3-C1 REPLAY LAUNDERING: a forged row linked to the anchored head,
            # stored under key K, then the writer's ordinary call with K.
            saved = ANCHOR.snapshot()
            cur.execute("savepoint replay_laundering")
            anchored_before = ANCHOR.head_params()
            real_head = head(cur)
            if (real_head[0], real_head[1]) != anchored_before:
                raise RuntimeError(f"replay laundering must start anchored: {real_head} / {anchored_before}")
            laundered_key = str(uuid.uuid4())
            actor(cur, WRITER)
            cur.execute("alter table ops.workflow_census_record disable trigger user")
            owner_insert(cur, real_head[0] + 1, real_head[1], "clock_timestamp()", principal=WRITER,
                         session=WRITER_LOGIN, payload=CENSUS, key=laundered_key)
            for name in GUARDS:
                cur.execute(f"alter table ops.workflow_census_record enable always trigger {name}")
            forged_row = head(cur)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            door_answer = cur.execute(
                "select seq, row_hash, replayed from ops.record_workflow_census(%s, %s, %s, %s)",
                (Jsonb(CENSUS), laundered_key, anchored_before[0], anchored_before[1])).fetchone()
            expect_refusal(cur, lambda: record(cur, key=laundered_key), (RuntimeError,),
                           "anchor_pending_missing_refused", "replay_laundering_same_key")
            become(cur, None)
            anchored_after = ANCHOR.head_params()
            laundered = verdict_of(cur)
            cur.execute("rollback to savepoint replay_laundering")
            ANCHOR.restore(saved)
            if door_answer is None or door_answer[2] is not True or door_answer[1] != forged_row[1]:
                raise RuntimeError(f"the door did not replay the forged row under its key: {door_answer}")
            if anchored_after != anchored_before:
                raise RuntimeError(f"a replayed forged row moved the anchor: {anchored_before} -> {anchored_after}")
            if (laundered.get("available"), laundered.get("reason")) != (False, "anchor_gap"):
                raise RuntimeError(f"a replayed forged row did not leave the reader at anchor_gap: {laundered}")

            # 13. R4-C1, A REPLACED WRITE DOOR. The owner replaces
            # ops.record_workflow_census with a copy that stores a forged payload.
            forged_literal = "'" + json.dumps(FORGED).replace("'", "''") + "'::jsonb"
            marker = "  v_found boolean;\nbegin\n"
            if door_source().count(marker) != 1:
                raise RuntimeError("the write door's body changed shape; update the replaced-door scenario")
            substituting = door_source().replace(marker, f"{marker}  p_payload := {forged_literal};\n")
            saved = ANCHOR.snapshot()
            cur.execute("savepoint replaced_door")
            anchored_before = ANCHOR.head_params()
            db_before = head(cur)
            cur.execute(substituting)
            # 13a. The door answers with the row it stored (forged payload): the
            # Worker's verification refuses it; nothing is registered, the write
            # rolls back, the anchor does not move.
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            pending_before = copy.deepcopy(ANCHOR.pending)
            expect_refusal(cur, lambda: record(cur), (RuntimeError,),
                           "'field': 'payload_sha256'", "replaced_door_forged_answer")
            become(cur, None)
            refused_state = (ANCHOR.head_params(), copy.deepcopy(ANCHOR.pending), head(cur))
            # 13b. The door lies instead: it stores the forged row but answers
            # with the honest row the Worker expects. The Worker verifies and
            # anchors the honest hash, which is not the stored row's: the reader
            # says tampered.
            def honest(row: tuple[Any, ...]) -> tuple[Any, ...]:
                sha = sha256_hex(canonical_json(CENSUS))
                return (row[0], row[1], row[2], row_hash({
                    "seq": row[0], "recorded_at": row[1], "principal": row[2],
                    "db_session_principal": WRITER_LOGIN, "prev_hash": row[4], "payload_sha256": sha}),
                    row[4], sha, False)
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            lied = record(cur, door=honest)
            become(cur, None)
            cur.execute(door_source())
            lying_door = verdict_of(cur)
            cur.execute("rollback to savepoint replaced_door")
            ANCHOR.restore(saved)
            if refused_state != (anchored_before, pending_before, db_before):
                raise RuntimeError(f"a replaced door's forged answer moved something: {refused_state} vs "
                                   f"{(anchored_before, pending_before, db_before)}")
            if lied[3] == head(cur)[1]:
                raise RuntimeError("scenario 13b did not separate the answered and stored rows")
            if (lying_door.get("available"), lying_door.get("reason")) != (False, "tampered"):
                raise RuntimeError(f"a lying door's forged row was not read tampered: {lying_door}")

            # 11. RE-ANCHOR: a crash whose key is lost, closed only by partner authority.
            saved = ANCHOR.snapshot()
            cur.execute("savepoint reanchor")
            become(cur, WRITER_LOGIN)
            actor(cur, WRITER)
            lost = record(cur, crash=True)
            expect_refusal(cur, lambda: reanchor(cur, (lost[0], lost[3]), "writer"),
                           (psycopg.errors.InsufficientPrivilege,), "permission denied",
                           "writer_cannot_reanchor")
            become(cur, AUTHORITY_LOGIN)
            actor(cur, "joe")
            cur.execute("select set_config('carr.verified_human_actor_slug', '', true)")
            expect_refusal(cur, lambda: reanchor(cur, (lost[0], lost[3]), "no partner"),
                           (psycopg.errors.RaiseException,), "workflow_census_reanchor_requires_partner",
                           "reanchor_requires_partner")
            cur.execute("select set_config('carr.verified_human_actor_slug', 'joe', true)")
            expect_refusal(cur, lambda: reanchor(cur, (lost[0] - 1, lost[4]), "stale review"),
                           (psycopg.errors.RaiseException,), "workflow_census_reanchor_head_moved",
                           "reanchor_head_moved")
            expect_refusal(cur, lambda: reanchor(cur, (lost[0], lost[3]), " "),
                           (psycopg.errors.RaiseException,), "workflow_census_reanchor_reason_required",
                           "reanchor_reason_required")
            receipt_key = str(uuid.uuid4())
            receipt = reanchor(cur, (lost[0], lost[3]), "writer lost the key of the last row", receipt_key)
            again = reanchor(cur, (lost[0], lost[3]), "writer lost the key of the last row", receipt_key)
            expect_refusal(cur, lambda: cur.execute("select count(*) from ops.workflow_census_reanchor_receipt"),
                           (psycopg.errors.InsufficientPrivilege,), "permission denied",
                           "authority_direct_receipt_select")
            become(cur, None)
            receipt_doc = {"receipt_id": receipt[0], "actor": receipt[2], "recorded_at": receipt[1],
                           "rows_reattested": receipt[9],
                           "old_head": {"seq": receipt[5], "row_hash": receipt[6]},
                           "new_head": {"seq": receipt[7], "row_hash": receipt[8]}}
            applied = ANCHOR.reanchor(receipt_doc)
            replay_applied = ANCHOR.reanchor(receipt_doc)
            reattested = verdict_of(cur)
            become(cur, AUTHORITY_LOGIN)
            actor(cur, "joe")
            cur.execute("select set_config('carr.verified_human_actor_slug', 'joe', true)")
            expect_refusal(cur, lambda: reanchor(cur, (lost[0], lost[3]), "again"),
                           (psycopg.errors.RaiseException,), "workflow_census_reanchor_not_needed",
                           "reanchor_not_needed")
            become(cur, None)
            receipts = cur.execute("select count(*) from ops.workflow_census_reanchor_receipt").fetchone()[0]
            expect_refusal(cur, lambda: cur.execute(
                "delete from ops.workflow_census_reanchor_receipt"),
                (psycopg.errors.RaiseException,), "append-only", "receipt_delete_refused")
            cur.execute("rollback to savepoint reanchor")
            ANCHOR.restore(saved)
            if (receipt[2], receipt[3], receipt[9], receipt[10]) != ("joe", "joe", 1, False) or again[10] is not True:
                raise RuntimeError(f"the re-anchor receipt is not the one expected: {receipt} / {again}")
            if applied.get("state") != "reanchored" or replay_applied.get("state") != "replayed":
                raise RuntimeError(f"the anchor did not take the receipt once: {applied} / {replay_applied}")
            if (reattested.get("available") is not True
                    or receipt[0] not in reattested.get("claim", "")
                    or reattested["attestation"]["last_reanchor"]["receipt_id"] != receipt[0]):
                raise RuntimeError(f"a re-anchored chain does not attest with its receipt named: {reattested}")
            if receipts != 1:
                raise RuntimeError(f"expected one receipt row, found {receipts}")

        print("PASS: workflow-census real-PostgreSQL: pinned and real hashes agree with the reader; "
              "writer and reader run as non-owner logins; the door refuses a missing or malformed "
              "principal, key reuse, fractions, wrong shape and oversize payloads; owner inserts that "
              "forge identity, splice, back-date, future-date or forge a digest are refused with every "
              "trigger on; guards are ENABLE ALWAYS and a flipped guard, a replaced guard body or a "
              "wholesale rewrite reads tampered and stays tampered through the next write; a crash "
              "gap reads anchor_gap until the same-key retry, then attests; a forged linked row replayed "
              "under its key moves nothing and reads anchor_gap; a replaced write door's forged row is "
              "refused by the Worker's verification or read tampered; the Worker's, the database's "
              "and the reader's canonical JSON agree; a re-anchor needs "
              "carr_authority and a verified partner and leaves a named receipt; freshness and "
              "truncation follow the database")
        return 0
    except Exception as exc:  # noqa: BLE001 - gate contract is a printed failure, not a traceback
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
