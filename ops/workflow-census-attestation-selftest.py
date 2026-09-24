#!/usr/bin/env python3
# doctrine: doctorcre-v5-astra-integration-review
"""Negative-path acceptance for the V5-F09 census route and its verifier.

WHAT IT DRIVES. ``lib/workflow_census_attestation.verify_census_chain`` directly,
and ``lib/control_plane_workflow_truth_reader`` through its module-private
``_bind_census_route`` with a forged server transport -- the same no-argument
route production binds to the Worker, over answers this suite writes.  Nothing
here touches a network or a database: every chain is built in Python with the
same hash rule migration 0595 uses (ops/workflow-census-local-pg-gate.py proves
the two agree byte for byte on a real PostgreSQL).

WHAT MUST HOLD, one check each:
  * a genuine, fresh chain from a listed writer is the ONLY input that answers
    ``available: true``, and even then the labels say "attested record", never a
    health word, and the claim says the observations inside are not proven;
  * TAMPERED payload, EDITED row (with and without a recomputed row hash),
    BACK-DATED row (edited in place, and a whole re-chained history that runs
    backwards), a FUTURE row, STALE latest row, UNKNOWN writer (principal and
    database session role), MISSING rows (empty chain, a deleted middle row, a
    missing payload, a truncated answer) and an unreachable or malformed server
    answer each fail closed with the named reason;
  * a broken chain outranks an unknown writer, which outranks staleness;
  * the route's answer is deep-frozen, takes no argument, and ignores anything
    written onto the reader module after it was bound;
  * the checked-in attestation config validates and carries the contract's
    26-hour window for a daily writer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ["CARR_WORKFLOW_CENSUS_OFFLINE"] = "1"  # hermetic: the real route never dials out

import lib.control_plane_workflow_truth_reader as reader  # noqa: E402
from lib import workflow_census_attestation as att  # noqa: E402

FAILURES: list[str] = []
PASSED = 0

# The standing rule's closed privileged union (ops/assurance-health-selftest.py
# PRIVILEGED_WORD_UNION), repeated here so this suite's label check does not
# depend on importing that file.
PRIVILEGED = ("allow", "commit", "prompt", "suppress", "release", "read", "covered",
              "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
              "satisfied", "complete", "admitted", "resumed", "attended", "verified",
              "present", "equivalent", "operational", "active", "green", "joins_exactly",
              "coverage_complete", "favorable")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
    else:
        FAILURES.append(f"{name}: {detail}")


WRITER = "joe-local"
SESSION = "carr_writer"
NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = att.load_config({
    "schema_version": "workflow-census-attestation.v1",
    "writer_principals": [WRITER], "writer_db_session_principals": [SESSION, "app_writer"],
    "writer_cadence_seconds": 86400, "freshness_window_seconds": 93600,
    "max_chain_rows": 20000})


def stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def census(tag: str) -> dict[str, Any]:
    return {"schema_version": "control-plane-workflow-truth.v1", "tag": tag,
            "rows": [{"workflow_key": "calendar-fetch-daily", "workflow_version": 5,
                      "state": "enabled_shadow_only"}],
            "summary": {"workflows": 1}}


def rehash(row: dict[str, Any]) -> dict[str, Any]:
    row["row_hash"] = att.row_hash(row)
    return row


def chain(times: list[datetime], *, principal: str = WRITER, session: str = SESSION,
          now: datetime = NOW) -> dict[str, Any]:
    """A genuine server answer: rows hashed exactly as migration 0595 hashes them."""
    rows: list[dict[str, Any]] = []
    payloads = [census(f"c{i}") for i in range(len(times))]
    for index, at in enumerate(times):
        rows.append(rehash({
            "seq": index + 1, "recorded_at": stamp(at), "principal": principal,
            "db_session_principal": session,
            "prev_hash": rows[-1]["row_hash"] if rows else None,
            "payload_sha256": att.sha256_hex(att.canonical_json(payloads[index])),
        }))
    return {"ok": True, "schema_version": "workflow-census-chain.v1", "server_now": stamp(now),
            "row_count": len(rows), "truncated": False, "chain": rows,
            "latest_payload": payloads[-1] if payloads else None}


def fresh() -> dict[str, Any]:
    return chain([NOW - timedelta(hours=50), NOW - timedelta(hours=26), NOW - timedelta(hours=2)])


def verdict(answer: Any) -> dict[str, Any]:
    return att.verify_census_chain(answer, CONFIG)


def refused(answer: Any, reason: str, detail: str) -> tuple[bool, str]:
    out = verdict(answer)
    return (out.get("available") is False and out.get("reason") == reason
            and out.get("detail") == detail and out.get("item_disposition") == "not_proven",
            json.dumps(out, default=str))


def main() -> int:
    # ---- the one input that answers available: true -------------------------
    good = verdict(fresh())
    check("a genuine, fresh chain from a listed writer is attested",
          good.get("available") is True and good.get("reason") == "census_attested"
          and good.get("item_disposition") == "attested_record_only"
          and good["attestation"]["seq"] == 3 and good["attestation"]["principal"] == WRITER
          and good["census"]["tag"] == "c2", json.dumps(good, default=str)[:300])
    check("the attested claim names the writer and the server time and disclaims the "
          "observations inside",
          good.get("claim", "").startswith(f"recorded by {WRITER} at ")
          and "unedited since" in good["claim"] and "not proven true" in good["claim"],
          good.get("claim", ""))
    vocabulary = [good["reason"], good["item_disposition"],
                  good["claim"].replace(WRITER, "").replace(good["attestation"]["recorded_at"], "")]
    hits = sorted({word for word in PRIVILEGED for text in vocabulary if word in text.lower()})
    check("the attested labels carry no word of the privileged union", not hits, json.dumps(hits))
    check("age is measured on the server's clock from the same answer",
          good["attestation"]["age_seconds"] == 7200
          and good["attestation"]["server_now"] == stamp(NOW), json.dumps(good["attestation"]))

    # ---- TAMPERED -----------------------------------------------------------
    answer = fresh()
    answer["latest_payload"]["rows"][0]["state"] = "operational"
    check("a tampered latest payload is a chain break", *refused(answer, "chain_break",
                                                                 "payload_digest_mismatch"))
    answer = fresh()
    answer["latest_payload"]["rows"][0]["state"] = "operational"
    answer["chain"][-1]["payload_sha256"] = att.sha256_hex(att.canonical_json(answer["latest_payload"]))
    check("a tampered payload with its digest recomputed breaks that row's hash",
          *refused(answer, "chain_break", "row_hash_mismatch"))
    answer = fresh()
    answer["chain"][-1]["payload_sha256"] = att.sha256_hex(att.canonical_json(answer["latest_payload"]))
    answer["latest_payload"]["summary"]["ratio"] = 0.5  # type: ignore[index]
    check("a fractional number in the payload is never hashed as if canonical",
          *refused(answer, "chain_break", "payload_digest_mismatch"))

    # ---- EDITED -------------------------------------------------------------
    answer = fresh()
    answer["chain"][1]["principal"] = "someone-else"
    check("an edited middle row without a recomputed hash is a chain break",
          *refused(answer, "chain_break", "row_hash_mismatch"))
    answer = fresh()
    answer["chain"][1]["principal"] = "someone-else"
    rehash(answer["chain"][1])
    check("an edited middle row WITH a recomputed hash breaks the next row's link",
          *refused(answer, "chain_break", "prev_hash_mismatch"))
    answer = fresh()
    answer["chain"][0]["prev_hash"] = "0" * 64
    rehash(answer["chain"][0])
    check("a genesis row that claims a predecessor is a chain break",
          *refused(answer, "chain_break", "prev_hash_mismatch"))

    # ---- BACK-DATED ---------------------------------------------------------
    answer = fresh()
    answer["chain"][-1]["recorded_at"] = stamp(NOW - timedelta(minutes=5))
    check("a latest row edited to look fresher breaks its own hash",
          *refused(answer, "chain_break", "row_hash_mismatch"))
    answer = chain([NOW - timedelta(hours=2), NOW - timedelta(hours=30), NOW - timedelta(hours=1)])
    check("a whole history re-chained so that time runs backwards is refused",
          *refused(answer, "chain_break", "time_regression"))
    answer = chain([NOW - timedelta(hours=3), NOW + timedelta(hours=1)])
    check("a row stamped after the server's own clock is refused",
          *refused(answer, "chain_break", "future_time"))

    # ---- STALE --------------------------------------------------------------
    answer = chain([NOW - timedelta(hours=60), NOW - timedelta(hours=27)])
    out = verdict(answer)
    check("a latest row older than the 26-hour window is stale, with its age",
          out.get("reason") == "stale" and out.get("available") is False
          and out.get("age_seconds") == 27 * 3600 and out.get("freshness_window_seconds") == 93600,
          json.dumps(out, default=str))
    edge = verdict(chain([NOW - timedelta(seconds=93600)]))
    check("a latest row exactly at the window edge is still inside it",
          edge.get("available") is True, json.dumps(edge, default=str)[:200])

    # ---- UNKNOWN WRITER -----------------------------------------------------
    check("an unlisted principal is an unknown writer",
          *refused(chain([NOW - timedelta(hours=1)], principal="dell-local"), "unknown_writer",
                   "principal_not_listed"))
    check("a listed principal through an unlisted database role (break-glass, owner) is an "
          "unknown writer",
          *refused(chain([NOW - timedelta(hours=1)], session="neondb_owner"), "unknown_writer",
                   "db_session_principal_not_listed"))

    # ---- MISSING ------------------------------------------------------------
    check("an empty store is unprovable, never attested",
          *refused({**fresh(), "chain": [], "row_count": 0, "latest_payload": None},
                   "handle_integrity_unprovable", "census_absent"))
    answer = fresh()
    del answer["chain"][1]
    answer["row_count"] = 2
    check("a deleted middle row is a sequence gap",
          *refused(answer, "chain_break", "seq_gap"))
    answer = fresh()
    answer["latest_payload"] = None
    check("a latest row with no payload is a chain break",
          *refused(answer, "chain_break", "payload_digest_mismatch"))
    check("a truncated answer is unprovable",
          *refused({**fresh(), "truncated": True}, "handle_integrity_unprovable", "chain_truncated"))
    check("a row count that disagrees with the rows served is unprovable",
          *refused({**fresh(), "row_count": 7}, "handle_integrity_unprovable", "chain_truncated"))

    # ---- MALFORMED ANSWERS --------------------------------------------------
    for label, shape in (("None", None), ("a list", []), ("ok false", {**fresh(), "ok": False}),
                         ("wrong schema", {**fresh(), "schema_version": "other"}),
                         ("no server clock", {**fresh(), "server_now": "yesterday"})):
        check(f"a malformed server answer ({label}) is unprovable",
              *refused(shape, "handle_integrity_unprovable", "server_answer_shape_refused"))
    answer = fresh()
    answer["chain"][2]["seq"] = True
    check("a boolean where a sequence number belongs is malformed, not 1",
          *refused(answer, "chain_break", "field_malformed"))

    # ---- ORDER OF TRUST -----------------------------------------------------
    answer = chain([NOW - timedelta(hours=60), NOW - timedelta(hours=40)], principal="dell-local")
    answer["chain"][0]["principal"] = "x"
    check("a broken chain outranks an unknown writer and staleness",
          verdict(answer).get("reason") == "chain_break", json.dumps(verdict(answer), default=str))
    check("an unknown writer outranks staleness",
          verdict(chain([NOW - timedelta(hours=40)], principal="dell-local")).get("reason")
          == "unknown_writer")

    # ---- THE ROUTE ----------------------------------------------------------
    served = fresh()
    route = reader._bind_census_route(lambda _max: (copy.deepcopy(served), None), lambda: CONFIG)
    answer = route()
    check("the bound route answers the attested census, deep-frozen",
          answer["available"] is True and isinstance(answer, MappingProxyType)
          and isinstance(answer["attestation"], MappingProxyType)
          and isinstance(answer["census"]["rows"], tuple)
          and answer["schema_version"] == reader.SCHEMA_VERSION, repr(answer)[:200])
    try:
        answer["available"] = False  # type: ignore[index]
        mutable = True
    except TypeError:
        mutable = False
    check("the route's answer refuses mutation", not mutable)
    code = reader.workflow_truth_census.__code__
    check("the public route takes no argument and looks up no global",
          code.co_argcount == 0 and code.co_kwonlyargcount == 0 and code.co_names == ()
          and set(code.co_freevars) == {"answer_for", "config_source", "transport"},
          json.dumps({"names": list(code.co_names), "free": list(code.co_freevars)}))
    setattr(reader, "_census_answer", lambda *_a: {"available": True, "reason": "caller"})
    setattr(reader, "_checked_in_config", lambda: {"writer_principals": ["caller"]})
    try:
        after = route()
        check("rebinding the reader's helpers after binding changes nothing",
              json.dumps(after, default=str) == json.dumps(answer, default=str)
              and after["reason"] == "census_attested", repr(after)[:200])
    finally:
        import importlib
        importlib.reload(reader)
    for label, transport, config_source, detail in (
        ("an unreachable server", lambda _m: (None, "server_route_unreachable"), lambda: CONFIG,
         "server_route_unreachable"),
        ("missing config", lambda _m: (fresh(), None),
         lambda: (_ for _ in ()).throw(FileNotFoundError("gone")), "attestation_config_unavailable"),
        ("a transport that raises", lambda _m: (_ for _ in ()).throw(RuntimeError("boom")),
         lambda: CONFIG, "route_fault"),
    ):
        out = reader._bind_census_route(transport, config_source)()
        check(f"the route fails closed on {label}",
              out["available"] is False and out["reason"] == "handle_integrity_unprovable"
              and out["detail"] == detail and out["item_disposition"] == "not_proven",
              repr(out))
    offline = reader.workflow_truth_census()
    check("the production route, offline by environment, fails closed without a network call",
          offline["available"] is False and offline["detail"] == "offline_by_environment"
          and offline["reason"] == "handle_integrity_unprovable", repr(offline))
    fail_closed_vocabulary = {offline["reason"], offline["item_disposition"], offline["detail"]}
    for detail in ("server_route_unreachable", "server_answer_unparseable",
                   "server_answer_shape_refused", "census_absent", "chain_truncated",
                   "attestation_config_unavailable", "route_fault", "seq_gap",
                   "prev_hash_mismatch", "row_hash_mismatch", "payload_digest_mismatch",
                   "time_regression", "future_time", "field_malformed", "principal_not_listed",
                   "db_session_principal_not_listed", "older_than_window", "chain_break",
                   "unknown_writer", "stale"):
        fail_closed_vocabulary.add(detail)
    hits = sorted({word for word in PRIVILEGED for text in fail_closed_vocabulary if word in text})
    check("no fail-closed reason or detail carries a word of the privileged union",
          not hits, json.dumps(hits))

    # ---- THE CHECKED-IN CONFIG ----------------------------------------------
    raw = json.loads((REPO / "ops" / "config" / "workflow-census-attestation.v1.json")
                     .read_text(encoding="utf-8"))
    loaded = att.load_config(raw)
    check("the checked-in config validates and carries the 26-hour window for a daily writer",
          loaded["freshness_window_seconds"] == 26 * 3600
          and loaded["writer_cadence_seconds"] == 86400
          and loaded["writer_principals"] == frozenset({"joe-local"}), json.dumps(raw)[:200])
    for label, bad in (("an empty writer list", {**raw, "writer_principals": []}),
                       ("a window no longer than the cadence",
                        {**raw, "freshness_window_seconds": 86400}),
                       ("a boolean window", {**raw, "freshness_window_seconds": True})):
        try:
            att.load_config(bad)
            accepted = True
        except att.AttestationConfigError:
            accepted = False
        check(f"a config with {label} is refused", not accepted)

    # ---- THE CANONICAL FORM THE DATABASE MUST MATCH -------------------------
    vector = {"b": [1, True, None, "é ☃ 日本 \n\t\"q\" \\ \u0001  "],
              "a": {"z": "𝄞", "y": 0, "é": "x", "ｚ": 1, "𝄞": 2}, "A": -12}
    check("canonical JSON sorts by code point and escapes exactly as the other two renderings do",
          att.canonical_json(vector) ==
          "{\"A\":-12,\"a\":{\"y\":0,\"z\":\"𝄞\",\"é\":\"x\",\"ｚ\":1,\"𝄞\":2},"
          "\"b\":[1,true,null,\"é ☃ 日本 \\n\\t\\\"q\\\" \\\\ \\u0001  \"]}"
          and hashlib.sha256(att.canonical_json(vector).encode()).hexdigest()
          == "92cd2cc4813ec6a8c9c0d164a594d670d6638a55d5716f0934572c9e49cdc2f1",
          att.canonical_json(vector))

    if FAILURES:
        for failure in FAILURES:
            print(f"FAIL {failure}")
        print(f"workflow-census-attestation-selftest: {len(FAILURES)} failed, {PASSED} passed")
        return 1
    print(f"workflow-census-attestation-selftest: {PASSED} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
