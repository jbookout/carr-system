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
  * a genuine, fresh chain from listed writers, with enforced guards and a head
    equal to the external anchor, is the ONLY input that answers
    ``available: true``, and even then the labels say "attested record", never a
    health word, and the claim states its three limits;
  * guards that are not ENABLE ALWAYS or whose function source no longer
    matches its pinned digest, a missing / behind / ahead / forked anchor, and a
    wholesale rewrite that re-chains cleanly but no longer matches the anchor
    each read ``tampered``; a chain exactly one linked row past the anchor reads
    ``anchor_gap``; an unreachable anchor is unprovable;
  * a row from an unlisted writer ANYWHERE in the chain fails the read;
  * TAMPERED payload, EDITED row (with and without a recomputed row hash),
    BACK-DATED row (edited in place, and a whole re-chained history that runs
    backwards), a FUTURE row, STALE latest row, UNKNOWN writer (principal and
    database session role), MISSING rows (empty chain, a deleted middle row, a
    missing payload, a truncated answer) and an unreachable or malformed server
    answer each fail closed with the named reason;
  * a broken chain outranks an unknown writer, which outranks staleness;
  * the route re-reads exactly once on ``anchor_gap``, a gap that persists
    stays ``anchor_gap``, and ``tampered`` is never re-read;
  * the writer re-sends the SAME key on any transport failure, accepts a late
    ``replayed``, and never retries a definitive refusal;
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
    "max_chain_rows": 20000,
    "guard_function_sha256": json.loads(
        (REPO / "ops" / "config" / "workflow-census-attestation.v1.json").read_text(encoding="utf-8")
    )["guard_function_sha256"]})
PINNED_GUARD_FUNCTIONS = dict(CONFIG["guard_function_sha256"])


_VECTOR_ROW = {"seq": 7, "recorded_at": "2026-09-24T04:10:00.123456Z", "principal": "joe-local",
               "db_session_principal": "carr_writer", "prev_hash": "a" * 64,
               "payload_sha256": "b" * 64}
ROW_HASH_VECTORS = (
    ("linked", _VECTOR_ROW, "d06373a7bf4840bfbc2fab8bae4caca4e2cd42cf289c116174460af02864186f"),
    ("genesis", {**_VECTOR_ROW, "seq": 1, "prev_hash": None},
     "c557fc0fa00e3c53b091055baa3eb469fc015e72f38e1b36a5e36ffae3f5be9d"),
)


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


ENFORCED = {name: "A" for name in att.GUARD_TRIGGERS}


def anchored(answer: dict[str, Any]) -> dict[str, Any]:
    """Point the answer's anchor at its own head, as the Worker would have."""
    rows = answer["chain"]
    answer["anchor"] = ({"state": "present", "seq": rows[-1]["seq"], "row_hash": rows[-1]["row_hash"],
                         "anchored_at": rows[-1]["recorded_at"]} if rows else {"state": "absent"})
    return answer


def rechain(answer: dict[str, Any]) -> dict[str, Any]:
    """Recompute every link and hash from row 1, the way a wholesale rewrite would."""
    for index, row in enumerate(answer["chain"]):
        row["prev_hash"] = answer["chain"][index - 1]["row_hash"] if index else None
        rehash(row)
    return answer


def chain(times: list[datetime], *, principal: str = WRITER, session: str = SESSION,
          now: datetime = NOW, principals: list[str] | None = None,
          latest_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """A genuine server answer: rows hashed exactly as migration 0595 hashes them,
    all three guards ENABLE ALWAYS, and the anchor at the chain's head."""
    rows: list[dict[str, Any]] = []
    payloads = [census(f"c{i}") for i in range(len(times))]
    if latest_payload is not None and payloads:
        payloads[-1] = latest_payload
    for index, at in enumerate(times):
        rows.append(rehash({
            "seq": index + 1, "recorded_at": stamp(at),
            "principal": principals[index] if principals else principal,
            "db_session_principal": session,
            "prev_hash": rows[-1]["row_hash"] if rows else None,
            "payload_sha256": att.sha256_hex(att.canonical_json(payloads[index])),
        }))
    return anchored({"ok": True, "schema_version": "workflow-census-chain.v1",
                     "server_now": stamp(now), "row_count": len(rows), "truncated": False,
                     "chain": rows, "latest_payload": payloads[-1] if payloads else None,
                     "guards": dict(ENFORCED),
                     "guard_functions": dict(PINNED_GUARD_FUNCTIONS)})


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
    check("the attested claim names the principal and the server time and states its limits",
          good.get("claim", "").startswith(f"recorded under principal {WRITER} at server time ")
          and "chain intact as served, and its head is the head the external anchor holds, "
              "which moves only to the next linked row the Worker itself inserted;" in good["claim"]
          and "re-anchored" not in good["claim"]
          and "not proof that the scheduled writer job wrote it" in good["claim"]
          and "not proof that the observations inside it are true" in good["claim"]
          and "not proof against a database owner who also replaces the doors that write or "
              "serve the chain" in good["claim"]
          and "not proof against a coordinated database-owner plus anchor rewrite" in good["claim"]
          and good["claim"].endswith("not proof against a re-anchor of a forged chain under "
                                     "partner authority, which a partner's local agent credential "
                                     "also carries: a re-anchor is recorded and named here, detected but "
                                     "not prevented"),
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
          *refused({**fresh(), "chain": [], "row_count": 0, "latest_payload": None,
                    "anchor": {"state": "absent"}},
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

    # ---- GUARDS NOT ENFORCED ---------------------------------------------------
    for label, state in (("re-enabled as an ordinary trigger ('O')", "O"),
                         ("disabled ('D')", "D"), ("missing", None)):
        answer = fresh()
        if state is None:
            del answer["guards"]["workflow_census_record_chain_guard"]
        else:
            answer["guards"]["workflow_census_record_chain_guard"] = state
        out = verdict(answer)
        check(f"a chain guard {label} reads tampered, before the chain is even walked",
              out.get("available") is False and out.get("reason") == "tampered"
              and out.get("detail") == "guard_not_enforced"
              and out.get("guards") == ["workflow_census_record_chain_guard"],
              json.dumps(out, default=str))
    check("an answer with no guard report is unprovable",
          *refused({k: v for k, v in fresh().items() if k != "guards"},
                   "handle_integrity_unprovable", "server_answer_shape_refused"))
    # The reviewer's probe H: the guard's BODY replaced while its trigger stays
    # ENABLE ALWAYS. Only the function digest shows it.
    for label, change in (("replaced", "0" * 64), ("missing", None)):
        answer = fresh()
        if change is None:
            del answer["guard_functions"]["workflow_census_record_chain_guard"]
        else:
            answer["guard_functions"]["workflow_census_record_chain_guard"] = change
        out = verdict(answer)
        check(f"a chain guard function {label} while its trigger stays 'A' reads tampered",
              out.get("available") is False and out.get("reason") == "tampered"
              and out.get("detail") == "guard_function_replaced"
              and out.get("guards") == ["workflow_census_record_chain_guard"],
              json.dumps(out, default=str))
    check("an answer with no guard function report is unprovable",
          *refused({k: v for k, v in fresh().items() if k != "guard_functions"},
                   "handle_integrity_unprovable", "server_answer_shape_refused"))

    # ---- THE ANCHOR OUTSIDE THE DATABASE --------------------------------------
    answer = fresh()
    answer["anchor"] = {"state": "absent"}
    check("rows the anchor never recorded read tampered",
          *refused(answer, "tampered", "anchor_absent"))
    check("an anchor that is unreachable is unprovable, never attested",
          *refused({**fresh(), "anchor": {"state": "unavailable", "detail": "anchor_unreachable"}},
                   "handle_integrity_unprovable", "anchor_unavailable"))
    check("an answer with no anchor at all is unprovable",
          *refused({k: v for k, v in fresh().items() if k != "anchor"},
                   "handle_integrity_unprovable", "server_answer_shape_refused"))
    answer = fresh()
    answer["anchor"]["seq"] = True
    check("a boolean anchor sequence is unprovable, not 1",
          *refused(answer, "handle_integrity_unprovable", "anchor_unavailable"))
    answer = fresh()
    answer["anchor"].update(seq=2, row_hash=answer["chain"][1]["row_hash"])
    check("a chain one linked row past its anchor (committed, never anchored) is an anchor gap",
          *refused(answer, "anchor_gap", "chain_one_linked_row_ahead_of_anchor"))
    answer = fresh()
    answer["anchor"].update(seq=1, row_hash=answer["chain"][0]["row_hash"])
    check("a chain two rows past its anchor reads tampered (the door refuses a second append)",
          *refused(answer, "tampered", "anchor_behind_chain"))
    answer = fresh()
    answer["anchor"].update(seq=2, row_hash="e" * 64)
    check("a chain one row past an anchor whose hash it does not hold reads tampered",
          *refused(answer, "tampered", "anchor_behind_chain"))
    answer = fresh()
    del answer["chain"][-1]
    answer["row_count"] = 2
    answer["latest_payload"] = census("c1")
    check("a chain whose newest anchored rows were deleted reads tampered",
          *refused(answer, "tampered", "chain_behind_anchor"))
    answer = fresh()
    answer["anchor"]["seq"] = 4
    check("an anchor naming the head's hash under another sequence number reads tampered",
          *refused(answer, "tampered", "chain_behind_anchor"))
    check("an empty store behind a present anchor reads tampered",
          *refused({**fresh(), "chain": [], "row_count": 0, "latest_payload": None},
                   "tampered", "chain_missing_behind_anchor"))
    # The reviewer's probe C, in miniature: disable the guards, delete the
    # history, write a new chain that verifies on its own, re-enable ALWAYS.
    answer = fresh()
    for row in answer["chain"]:
        row["recorded_at"] = stamp(datetime.strptime(row["recorded_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
                                   .replace(tzinfo=timezone.utc) + timedelta(seconds=1))
    rechain(answer)
    inner = att.verify_census_chain({**answer, "anchor": anchored(copy.deepcopy(answer))["anchor"]},
                                    CONFIG)
    check("control: the rewritten history verifies on its own (so only the anchor catches it)",
          inner.get("available") is True, json.dumps(inner, default=str)[:200])
    check("a wholesale rewrite that re-chains cleanly no longer matches the anchor: tampered",
          *refused(answer, "tampered", "anchor_hash_mismatch"))
    # ...and the next ordinary write cannot carry it forward: under strict
    # linkage the anchor stays on the old head, so the rewritten chain plus one
    # new row is still not the anchored chain plus one.
    grown = copy.deepcopy(answer)
    grown["chain"].append(rehash({"seq": 4, "recorded_at": stamp(NOW - timedelta(hours=1)),
                                  "principal": WRITER, "db_session_principal": SESSION,
                                  "prev_hash": grown["chain"][-1]["row_hash"],
                                  "payload_sha256": att.sha256_hex(att.canonical_json(census("c4")))}))
    grown["row_count"], grown["latest_payload"] = 4, census("c4")
    check("a rewrite plus one more row, beside the anchor that never moved, reads tampered",
          *refused(grown, "tampered", "anchor_behind_chain"))

    # ---- A RE-ANCHORED CHAIN SAYS SO -----------------------------------------
    answer = fresh()
    answer["anchor"]["last_reanchor"] = {
        "receipt_id": "0f0e0d0c-0b0a-4908-8706-050403020100", "actor": "joe",
        "recorded_at": stamp(NOW - timedelta(hours=3)), "rows_reattested": 1,
        "old_head": None, "new_head": None}
    out = verdict(answer)
    check("an attestation over a re-anchored chain names the receipt, actor and rows in its claim",
          out.get("available") is True
          and "its anchor was last re-anchored under receipt 0f0e0d0c-0b0a-4908-8706-050403020100 "
              "by joe at " in out.get("claim", "")
          and "over 1 rows the anchor had not vouched for" in out.get("claim", "")
          and out["attestation"]["last_reanchor"]["receipt_id"].startswith("0f0e0d0c"),
          json.dumps(out, default=str)[:400])
    words = out.get("claim", "").replace(WRITER, "").replace(out["attestation"]["recorded_at"], "")
    check("the re-anchor clause carries no word of the privileged union",
          not [w for w in PRIVILEGED if w in words.lower()],
          json.dumps([w for w in PRIVILEGED if w in words.lower()]))
    answer["anchor"]["last_reanchor"] = {"receipt_id": "", "actor": "joe"}
    check("a malformed re-anchor record is unprovable, never attested",
          *refused(answer, "handle_integrity_unprovable", "anchor_unavailable"))

    # ---- AN UNLISTED WRITER ANYWHERE ------------------------------------------
    out = verdict(chain([NOW - timedelta(hours=50), NOW - timedelta(hours=26),
                         NOW - timedelta(hours=2)], principals=[WRITER, "dell-local", WRITER]))
    check("a middle row from an unlisted principal fails the read, at that row",
          out.get("reason") == "unknown_writer" and out.get("detail") == "principal_not_listed"
          and out.get("at_seq") == 2, json.dumps(out, default=str))
    answer = chain([NOW - timedelta(hours=50), NOW - timedelta(hours=2)])
    answer["chain"][0]["db_session_principal"] = "neondb_owner"
    rechain(answer)
    anchored(answer)
    out = verdict(answer)
    check("a first row written under an unlisted login role fails the read, at that row",
          out.get("reason") == "unknown_writer"
          and out.get("detail") == "db_session_principal_not_listed" and out.get("at_seq") == 1,
          json.dumps(out, default=str))

    # ---- WHAT THE VERIFIER REFUSES EVEN WHEN THE HASHES ARE GENUINE ------------
    small = att.load_config({"schema_version": "workflow-census-attestation.v1",
                             "writer_principals": [WRITER],
                             "writer_db_session_principals": [SESSION],
                             "writer_cadence_seconds": 86400, "freshness_window_seconds": 93600,
                             "max_chain_rows": 2,
                             "guard_function_sha256": PINNED_GUARD_FUNCTIONS})
    out = att.verify_census_chain(fresh(), small)
    check("a chain longer than max_chain_rows is refused as truncated",
          out.get("reason") == "handle_integrity_unprovable" and out.get("detail") == "chain_truncated",
          json.dumps(out, default=str))
    check("a genuinely hashed latest payload of another schema is refused",
          *refused(chain([NOW - timedelta(hours=1)],
                         latest_payload={**census("x"), "schema_version": "other.v1"}),
                   "chain_break", "payload_digest_mismatch"))
    check("a genuinely hashed latest payload holding a fraction is refused",
          *refused(chain([NOW - timedelta(hours=1)],
                         latest_payload={**census("x"), "summary": {"ratio": 0.5}}),
                   "chain_break", "payload_digest_mismatch"))
    check("a genuinely hashed row whose principal is not an actor slug is malformed",
          *refused(chain([NOW - timedelta(hours=1)], principal="Joe Local"),
                   "chain_break", "field_malformed"))
    answer = chain([NOW - timedelta(hours=2), NOW - timedelta(hours=1)])
    answer["chain"][-1]["recorded_at"] = (NOW - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.") + "123Z"
    rechain(answer)
    anchored(answer)
    check("a genuinely hashed time with three fractional digits is malformed",
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
    # ---- ONE RE-READ, FOR ONE RACE --------------------------------------------
    racing = fresh()
    racing["anchor"].update(seq=2, row_hash=racing["chain"][1]["row_hash"])
    served_list = [racing, fresh()]
    pauses: list[int] = []
    routed = reader._census_answer(lambda _m: (served_list.pop(0), None), lambda: CONFIG,
                                   pause=lambda: pauses.append(1))
    check("an anchor gap is re-read once and then attested",
          routed["available"] is True and pauses == [1] and not served_list, repr(routed)[:200])
    calls: list[int] = []

    def persistent(_m: int) -> tuple[dict[str, Any], None]:
        calls.append(1)
        stuck = fresh()
        stuck["anchor"].update(seq=2, row_hash=stuck["chain"][1]["row_hash"])
        return stuck, None
    routed = reader._census_answer(persistent, lambda: CONFIG, pause=lambda: None)
    check("a gap that persists stays anchor_gap after exactly one re-read",
          routed["available"] is False and routed["reason"] == "anchor_gap" and len(calls) == 2,
          repr(routed)[:200])
    for label, mutate in (
        ("anchor hash mismatch", lambda a: a["anchor"].update(row_hash="f" * 64)),
        ("chain two rows past the anchor",
         lambda a: a["anchor"].update(seq=1, row_hash=a["chain"][0]["row_hash"])),
        ("replaced guard function",
         lambda a: a["guard_functions"].update(workflow_census_record_append_only="0" * 64)),
    ):
        calls.clear()
        tampered = fresh()
        mutate(tampered)

        def once_tampered(_m: int, _t: dict[str, Any] = tampered) -> tuple[dict[str, Any], None]:
            calls.append(1)
            return copy.deepcopy(_t), None
        routed = reader._census_answer(once_tampered, lambda: CONFIG, pause=lambda: None)
        check(f"tampered ({label}) is never re-read",
              routed["reason"] == "tampered" and len(calls) == 1, repr(routed)[:200])

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
                   "unknown_writer", "stale", "tampered", "guard_not_enforced", "anchor_absent",
                   "anchor_unavailable", "anchor_behind_chain", "chain_behind_anchor",
                   "anchor_hash_mismatch", "chain_missing_behind_anchor", "anchor_gap",
                   "chain_one_linked_row_ahead_of_anchor", "guard_function_replaced"):
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
                       ("a boolean window", {**raw, "freshness_window_seconds": True}),
                       ("no guard function pins",
                        {k: v for k, v in raw.items() if k != "guard_function_sha256"}),
                       ("a guard function pin missing",
                        {**raw, "guard_function_sha256": {
                            k: v for k, v in raw["guard_function_sha256"].items()
                            if k != "workflow_census_record_no_truncate"}}),
                       ("a guard function pin that is not a digest",
                        {**raw, "guard_function_sha256": {
                            **raw["guard_function_sha256"],
                            "workflow_census_record_chain_guard": "not-a-digest"}})):
        try:
            att.load_config(bad)
            accepted = True
        except att.AttestationConfigError:
            accepted = False
        check(f"a config with {label} is refused", not accepted)

    # The pins are the digest of each guard function's source exactly as
    # migration 0595 writes it: "<schema>.<name>" + newline + the text between
    # the dollar quotes (pg_proc.prosrc). The local Postgres gate checks the
    # same pins against a real database's read door.
    import re as _re
    migration = (REPO / "migrations" / "0595_workflow_census_store.sql").read_text(encoding="utf-8")
    trigger_functions = dict(_re.findall(
        r"create trigger (workflow_census_record_\w+)\n[^;]*?execute function ops\.(\w+)\(\);",
        migration))
    recomputed = {}
    for trigger, function in trigger_functions.items():
        body = _re.search(r"create or replace function ops\." + function
                          + r"\(\)\nreturns trigger\n.*?as \$\$(.*?)\$\$;", migration, _re.S)
        recomputed[trigger] = (hashlib.sha256(f"ops.{function}\n{body.group(1)}".encode("utf-8"))
                               .hexdigest() if body else None)
    check("the pinned guard function digests are the migration's own guard sources",
          recomputed == raw["guard_function_sha256"] and set(recomputed) == set(att.GUARD_TRIGGERS),
          json.dumps(recomputed))

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

    # ---- THE WRITER'S ONE RETRY, FOR A FAILED ANCHOR ADVANCE -------------------
    import importlib.util
    spec = importlib.util.spec_from_file_location("census_writer", REPO / "ops" / "workflow-census-writer.py")
    assert spec is not None and spec.loader is not None
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)

    class _Proc:
        def __init__(self, code: int, out: str = "", err: str = "") -> None:
            self.returncode, self.stdout, self.stderr = code, out, err

    def scripted(*answers: _Proc) -> tuple[list[str], Any]:
        keys: list[str] = []
        queue = list(answers)

        def run(argv: list[str], **_kw: Any) -> _Proc:
            keys.append(json.loads(argv[-1])["idempotency_key"])
            return queue.pop(0)
        return keys, run

    ok_answer = json.dumps({"ok": True, "seq": 1, "row_hash": "1" * 64, "anchor": "replayed"})
    original_run = writer.subprocess.run
    pauses_taken: list[float] = []
    no_wait = pauses_taken.append

    try:
        for label, first in (
            ("a failed anchor advance",
             _Proc(1, err="ToolError workflow_census_anchor_not_advanced anchor_unreachable")),
            ("an unreadable anchor", _Proc(1, err="ToolError workflow_census_anchor_unavailable")),
            ("a Worker fault", _Proc(1, err="HTTP 502 upstream")),
            ("an unparseable answer", _Proc(0, "<html>")),
        ):
            keys, writer.subprocess.run = scripted(first, _Proc(0, ok_answer))
            out = writer.record_census(census("w"), pause=no_wait)
            check(f"{label} is re-sent under the SAME idempotency key, and a late replayed "
                  "anchor answer is accepted",
                  out["anchor"] == "replayed" and len(keys) == 2 and keys[0] == keys[1],
                  json.dumps(keys))
        flaky_keys: list[str] = []
        queue: list[Any] = [TimeoutError("slow"), _Proc(0, ok_answer)]

        def flaky(argv: list[str], **_kw: Any) -> _Proc:
            flaky_keys.append(json.loads(argv[-1])["idempotency_key"])
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        writer.subprocess.run = flaky
        out = writer.record_census(census("w"), pause=no_wait)
        check("a subprocess that times out is re-sent under the SAME idempotency key",
              out["anchor"] == "replayed" and len(flaky_keys) == 2 and flaky_keys[0] == flaky_keys[1],
              json.dumps(flaky_keys))
        keys, writer.subprocess.run = scripted(*[_Proc(1, err="HTTP 502 upstream")] * writer.ATTEMPTS)
        try:
            writer.record_census(census("w"), pause=no_wait)
            gave_up = False
        except writer.WriterRefusal:
            gave_up = len(keys) == writer.ATTEMPTS and len(set(keys)) == 1
        check(f"a transport failure on every one of {writer.ATTEMPTS} attempts fails the run, "
              "all under one key", gave_up, json.dumps(keys))
        for name in ("workflow_census_key_reuse", "workflow_census_tampered",
                     "workflow_census_anchor_gap"):
            keys, writer.subprocess.run = scripted(_Proc(1, err=f"ToolError {name}"))
            try:
                writer.record_census(census("w"), pause=no_wait)
                refused_once = False
            except writer.WriterRefusal:
                refused_once = len(keys) == 1
            check(f"a definitive refusal ({name}) is not retried", refused_once, json.dumps(keys))
        keys, writer.subprocess.run = scripted(
            *[_Proc(0, json.dumps({"ok": True, "seq": 1, "row_hash": "1" * 64}))] * writer.ATTEMPTS)
        try:
            writer.record_census(census("w"), pause=no_wait)
            unconfirmed = False
        except writer.WriterRefusal as refusal:
            unconfirmed = "anchor_unconfirmed" in str(refusal)
        check("a write answer that never confirms the anchor fails the run", unconfirmed)
    finally:
        writer.subprocess.run = original_run

    # ---- THE ROW-HASH RULE, PINNED --------------------------------------------
    # The chains above are hashed by att.row_hash itself, so a change to the
    # rule (dropping a field, say) would move both sides at once. These two
    # vectors pin the rule; ops/workflow-census-local-pg-gate.py asserts the
    # database's ops.workflow_census_row_hash returns the same two digests.
    for label, vector_row, digest in ROW_HASH_VECTORS:
        check(f"the row-hash rule reproduces the pinned {label} vector",
              att.row_hash(vector_row) == digest, att.row_hash(vector_row))

    if FAILURES:
        for failure in FAILURES:
            print(f"FAIL {failure}")
        print(f"workflow-census-attestation-selftest: {len(FAILURES)} failed, {PASSED} passed")
        return 1
    print(f"workflow-census-attestation-selftest: {PASSED} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
