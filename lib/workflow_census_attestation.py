"""Verify a V5-F09 workflow census chain served by the record layer.

WHAT THIS IS. A pure function from the server's census-chain answer (the
``read-workflow-census`` verb, migration 0595's ``ops.read_workflow_census``)
plus the checked-in attestation config to one verdict.  It performs no I/O.
``lib/control_plane_workflow_truth_reader`` is its one production caller; the
negative-path suite drives it directly with forged answers.

WHAT A PASSING VERDICT MEANS, AND ONLY THIS.  The latest census row was recorded
under the named principal at the named server time; the chain is intact as
served, from row 1 to that row; every row in it was written under a listed
principal and a listed database login role; the store's three guard triggers
are ENABLE ALWAYS and each still calls the function source pinned in config;
and the chain's head equals the head held by the external anchor (a Durable
Object, ``mcp-server/src/workflow-census-anchor.js``), which only ever moves
to the next linked row (seq + 1 whose prev_hash is the anchored row_hash) or,
on the record, by a partner-authority re-anchor receipt.

WHAT IT DOES NOT MEAN.
  * The census is not proven TRUE: the scheduler observations, acceptance rows
    and completion evidence inside it are whatever the writer read.
  * An allowlisted principal is not proof that the scheduled writer job wrote
    the row.  The principal is the actor behind a bearer token, and the local
    token on the writer's Mac is readable by anything running as that user
    there; any such process can call the write verb under the same principal.
  * It is not proof against a COORDINATED rewrite: someone holding the database
    owner role AND able to deploy Worker code that rewrites the anchor could
    replace both consistently.  Nor against an owner who also replaces the
    database door that serves the chain, so it answers with the anchored
    chain while the stored rows differ: every check here runs over what that
    door serves.  Nor against a partner who re-anchors a forged chain: that act
    leaves a receipt and the anchor then names it (``last_reanchor``), but it
    is not prevented.
Every output label below is chosen to say "attested record", never a health
word, and the ``claim`` sentence says the three limits out loud.

THE CHAIN, restated from the migration so a reader of this file need not open
it.  For each row::

    payload_sha256 = sha256(canonical(latest payload))      -- latest row only
    row_hash       = sha256(canonical({"db_session_principal", "payload_sha256",
                                       "prev_hash", "principal", "recorded_at",
                                       "seq"}))
    prev_hash      = previous row's row_hash, null only at seq 1

where canonical(x) is ``json.dumps(x, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)``, byte-equal to the database's ``ops.scac_canonical_json``
for the value kinds the door admits (no fractional numbers).

THE ORDER OF REFUSALS IS THE ORDER OF TRUST.  Guards that are not enforced
make the stored chain meaningless, so ``tampered`` (guard_not_enforced) comes
first after the answer's shape, and a guard function whose source no longer
matches its pinned digest is ``tampered`` (guard_function_replaced) right after.  A chain that does not verify says nothing about
who wrote it, so ``chain_break`` is decided before the anchor and before
``unknown_writer``; a chain exactly one linked row ahead of the anchor is
``anchor_gap`` (a committed write whose anchor advance has not landed yet: the
writer's retry of the same key, or a re-read after a concurrent write, clears
it); a chain that verifies but otherwise does not match the anchor is
``tampered``; a record whose writers are not all listed says nothing about
freshness, so ``unknown_writer`` is decided before ``stale``.

WHY ``chain_break`` AND NOT ``chain_broken``.  The contract named the reason
``chain_broken``.  The standing rule's closed privileged union forbids "ok" even
as a substring of an exported string (ops/assurance-health-selftest.py
PRIVILEGED_WORD_UNION), and "broken" carries it -- the same reason the older
reason id stopped being spelled with "reading".  Same meaning, word-clean
spelling.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

CHAIN_SCHEMA_VERSION = "workflow-census-chain.v1"
CENSUS_SCHEMA_VERSION = "control-plane-workflow-truth.v1"
CONFIG_SCHEMA_VERSION = "workflow-census-attestation.v1"

# Fail-closed reasons.  The first is today's reason code, kept verbatim; the
# other three are the contract's specific ones.
REASON_UNPROVABLE = "handle_integrity_unprovable"
REASON_CHAIN_BREAK = "chain_break"
REASON_UNKNOWN_WRITER = "unknown_writer"
REASON_STALE = "stale"
REASON_TAMPERED = "tampered"
REASON_ANCHOR_GAP = "anchor_gap"
REASON_ATTESTED = "census_attested"

# The store's triggers (migration 0595), each of which must be ENABLE ALWAYS
# ('A').  'O' is what a plain ENABLE TRIGGER leaves after a DISABLE; 'D' is off.
GUARD_TRIGGERS = ("workflow_census_record_append_only",
                  "workflow_census_record_chain_guard",
                  "workflow_census_record_no_truncate")

DISPOSITION_NOT_PROVEN = "not_proven"
DISPOSITION_ATTESTED = "attested_record_only"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_DB_ROLE = re.compile(r"^[a-z_][a-z0-9_$]{0,62}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class AttestationConfigError(ValueError):
    """The checked-in attestation config is missing or malformed."""


def canonical_json(value: Any) -> str:
    """The one canonical serialization, shared with the database's."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_hash(row: Mapping[str, Any]) -> str:
    """Recompute one chain row's row_hash from its own fields."""
    return sha256_hex(canonical_json({
        "seq": row["seq"],
        "recorded_at": row["recorded_at"],
        "principal": row["principal"],
        "db_session_principal": row["db_session_principal"],
        "prev_hash": row["prev_hash"],
        "payload_sha256": row["payload_sha256"],
    }))


def _has_fraction(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return True
    if isinstance(value, list):
        return any(_has_fraction(item) for item in value)
    if isinstance(value, dict):
        return any(_has_fraction(item) for item in value.values())
    return False


def _time(text: Any) -> datetime | None:
    if not isinstance(text, str) or not _TIME.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_config(raw: Any) -> dict[str, Any]:
    """Validate the parsed attestation config; raise rather than guess."""
    if not isinstance(raw, dict) or raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise AttestationConfigError("attestation config schema_version mismatch")
    writers = raw.get("writer_principals")
    sessions = raw.get("writer_db_session_principals")
    window = raw.get("freshness_window_seconds")
    cadence = raw.get("writer_cadence_seconds")
    max_rows = raw.get("max_chain_rows")
    digests = raw.get("guard_function_sha256")
    if (not isinstance(writers, list) or not writers
            or not all(isinstance(w, str) and _SLUG.match(w) for w in writers)):
        raise AttestationConfigError("writer_principals must be a non-empty list of actor slugs")
    if (not isinstance(sessions, list) or not sessions
            or not all(isinstance(s, str) and _DB_ROLE.match(s) for s in sessions)):
        raise AttestationConfigError("writer_db_session_principals must be a non-empty list of roles")
    for name, value in (("freshness_window_seconds", window),
                        ("writer_cadence_seconds", cadence),
                        ("max_chain_rows", max_rows)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AttestationConfigError(f"{name} must be a positive integer")
    if not isinstance(window, int) or not isinstance(cadence, int) or window <= cadence:
        raise AttestationConfigError("freshness_window_seconds must exceed writer_cadence_seconds")
    if (not isinstance(digests, dict) or set(digests) != set(GUARD_TRIGGERS)
            or not all(isinstance(d, str) and _HEX64.match(d) for d in digests.values())):
        raise AttestationConfigError("guard_function_sha256 must pin one sha256 per guard trigger")
    return {"writer_principals": frozenset(writers),
            "writer_db_session_principals": frozenset(sessions),
            "freshness_window_seconds": window,
            "writer_cadence_seconds": cadence,
            "max_chain_rows": max_rows,
            "guard_function_sha256": dict(digests)}


def _reanchor_shape(value: Any) -> bool:
    """The anchor's last re-anchor record, as the Durable Object stores it."""
    if not isinstance(value, dict):
        return False
    count = value.get("rows_reattested")
    return (isinstance(value.get("receipt_id"), str) and bool(value["receipt_id"])
            and isinstance(value.get("actor"), str) and bool(_SLUG.match(value["actor"]))
            and isinstance(value.get("recorded_at"), str)
            and not isinstance(count, bool) and isinstance(count, int) and count >= 0)


def refusal(reason: str, detail: str, **extra: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {"available": False, "reason": reason,
                              "item_disposition": DISPOSITION_NOT_PROVEN, "detail": detail}
    answer.update(extra)
    return answer


def verify_census_chain(answer: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    """Decide one verdict for one server answer.  Never raises on a bad answer."""
    # ---- the answer's shape --------------------------------------------------
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        return refusal(REASON_UNPROVABLE, "server_answer_shape_refused")
    if answer.get("schema_version") != CHAIN_SCHEMA_VERSION:
        return refusal(REASON_UNPROVABLE, "server_answer_shape_refused")
    chain = answer.get("chain")
    server_now = _time(answer.get("server_now"))
    row_count = answer.get("row_count")
    if (not isinstance(chain, list) or server_now is None
            or isinstance(row_count, bool) or not isinstance(row_count, int)
            or not isinstance(answer.get("truncated"), bool)):
        return refusal(REASON_UNPROVABLE, "server_answer_shape_refused")
    guards = answer.get("guards")
    anchor = answer.get("anchor")
    if not isinstance(guards, dict) or not isinstance(anchor, dict):
        return refusal(REASON_UNPROVABLE, "server_answer_shape_refused")
    unenforced = sorted(name for name in GUARD_TRIGGERS if guards.get(name) != "A")
    if unenforced:
        return refusal(REASON_TAMPERED, "guard_not_enforced", guards=unenforced)
    functions = answer.get("guard_functions")
    if not isinstance(functions, dict):
        return refusal(REASON_UNPROVABLE, "server_answer_shape_refused")
    pinned = config["guard_function_sha256"]
    replaced = sorted(name for name in GUARD_TRIGGERS if functions.get(name) != pinned[name])
    if replaced:
        return refusal(REASON_TAMPERED, "guard_function_replaced", guards=replaced)
    if answer["truncated"] or row_count != len(chain) or len(chain) > config["max_chain_rows"]:
        return refusal(REASON_UNPROVABLE, "chain_truncated")
    anchor_state = anchor.get("state")
    if anchor_state not in ("present", "absent"):
        return refusal(REASON_UNPROVABLE, "anchor_unavailable")
    if not chain:
        if anchor_state == "present":
            # The Worker anchored a head the database no longer holds.
            return refusal(REASON_TAMPERED, "chain_missing_behind_anchor",
                           anchored_seq=anchor.get("seq"))
        return refusal(REASON_UNPROVABLE, "census_absent")

    # ---- the chain, row 1 to the latest ---------------------------------------
    previous: dict[str, Any] | None = None
    previous_time: datetime | None = None
    for index, row in enumerate(chain):
        seq = index + 1
        if not isinstance(row, dict):
            return refusal(REASON_CHAIN_BREAK, "field_malformed", at_seq=seq)
        recorded = _time(row.get("recorded_at"))
        if (isinstance(row.get("seq"), bool) or not isinstance(row.get("seq"), int)
                or recorded is None
                or not isinstance(row.get("principal"), str)
                or not _SLUG.match(row["principal"])
                or not isinstance(row.get("db_session_principal"), str)
                or not _DB_ROLE.match(row["db_session_principal"])
                or not isinstance(row.get("payload_sha256"), str)
                or not _HEX64.match(row["payload_sha256"])
                or not isinstance(row.get("row_hash"), str)
                or not _HEX64.match(row["row_hash"])
                or not (row.get("prev_hash") is None
                        or (isinstance(row.get("prev_hash"), str)
                            and _HEX64.match(row["prev_hash"])))):
            return refusal(REASON_CHAIN_BREAK, "field_malformed", at_seq=seq)
        if row["seq"] != seq:
            return refusal(REASON_CHAIN_BREAK, "seq_gap", at_seq=seq)
        expected_prev = None if previous is None else previous["row_hash"]
        if row["prev_hash"] != expected_prev:
            return refusal(REASON_CHAIN_BREAK, "prev_hash_mismatch", at_seq=seq)
        if row_hash(row) != row["row_hash"]:
            return refusal(REASON_CHAIN_BREAK, "row_hash_mismatch", at_seq=seq)
        if previous_time is not None and recorded < previous_time:
            return refusal(REASON_CHAIN_BREAK, "time_regression", at_seq=seq)
        if recorded > server_now:
            return refusal(REASON_CHAIN_BREAK, "future_time", at_seq=seq)
        previous, previous_time = row, recorded

    latest = chain[-1]
    payload = answer.get("latest_payload")
    if (not isinstance(payload, dict) or _has_fraction(payload)
            or payload.get("schema_version") != CENSUS_SCHEMA_VERSION):
        return refusal(REASON_CHAIN_BREAK, "payload_digest_mismatch", at_seq=latest["seq"])
    try:
        payload_digest = sha256_hex(canonical_json(payload))
    except (TypeError, ValueError):
        return refusal(REASON_CHAIN_BREAK, "payload_digest_mismatch", at_seq=latest["seq"])
    if payload_digest != latest["payload_sha256"]:
        return refusal(REASON_CHAIN_BREAK, "payload_digest_mismatch", at_seq=latest["seq"])

    # ---- the anchor outside the database --------------------------------------
    if anchor_state != "present":
        return refusal(REASON_TAMPERED, "anchor_absent", chain_seq=latest["seq"])
    anchored_seq, anchored_hash = anchor.get("seq"), anchor.get("row_hash")
    if (isinstance(anchored_seq, bool) or not isinstance(anchored_seq, int)
            or not isinstance(anchored_hash, str) or not _HEX64.match(anchored_hash)):
        return refusal(REASON_UNPROVABLE, "anchor_unavailable")
    if anchored_seq != latest["seq"] or anchored_hash != latest["row_hash"]:
        # The one legitimate disagreement: the chain is exactly one row past the
        # anchored head and still holds that head unchanged, so the new row
        # links to it.  The write door refuses every further append in this
        # state, so the chain can never be more than one such row ahead.
        if (anchored_seq == latest["seq"] - 1 and anchored_seq >= 1
                and chain[anchored_seq - 1]["row_hash"] == anchored_hash):
            return refusal(REASON_ANCHOR_GAP, "chain_one_linked_row_ahead_of_anchor",
                           chain_seq=latest["seq"], anchored_seq=anchored_seq)
        if anchored_seq < latest["seq"]:
            detail = "anchor_behind_chain"
        elif anchored_seq > latest["seq"]:
            detail = "chain_behind_anchor"
        else:
            detail = "anchor_hash_mismatch"
        return refusal(REASON_TAMPERED, detail, chain_seq=latest["seq"],
                       anchored_seq=anchored_seq)
    last_reanchor = anchor.get("last_reanchor")
    if last_reanchor is not None and not _reanchor_shape(last_reanchor):
        return refusal(REASON_UNPROVABLE, "anchor_unavailable")

    # ---- who wrote every row ---------------------------------------------------
    # Every row, not only the latest: a row from an unlisted writer anywhere in
    # the chain means someone outside the lists wrote to the store.
    for row in chain:
        if row["principal"] not in config["writer_principals"]:
            return refusal(REASON_UNKNOWN_WRITER, "principal_not_listed", at_seq=row["seq"])
        if row["db_session_principal"] not in config["writer_db_session_principals"]:
            return refusal(REASON_UNKNOWN_WRITER, "db_session_principal_not_listed",
                           at_seq=row["seq"])

    # ---- how old it is, on the server's clock ---------------------------------
    latest_time = previous_time
    assert latest_time is not None
    age = int((server_now - latest_time).total_seconds())
    window = config["freshness_window_seconds"]
    if age > window:
        return refusal(REASON_STALE, "older_than_window", at_seq=latest["seq"],
                       age_seconds=age, freshness_window_seconds=window)

    reanchor_clause = ""
    if last_reanchor is not None:
        reanchor_clause = (f"; its anchor was last re-anchored under receipt "
                           f"{last_reanchor['receipt_id']} by {last_reanchor['actor']} at "
                           f"{last_reanchor['recorded_at']}, over "
                           f"{last_reanchor['rows_reattested']} rows the anchor had not vouched for")
    return {
        "available": True,
        "reason": REASON_ATTESTED,
        "item_disposition": DISPOSITION_ATTESTED,
        "claim": (f"recorded under principal {latest['principal']} at server time "
                  f"{latest['recorded_at']}; chain intact as served, and its head is the head "
                  "the external anchor holds, which moves only to the next linked row"
                  f"{reanchor_clause}; not proof that the scheduled writer job wrote it, not "
                  "proof that the observations inside it are true, not proof against a database "
                  "owner who also replaces the door that serves the chain, not proof against a "
                  "coordinated database-owner plus anchor rewrite, and not proof against a "
                  "partner-authority re-anchor of a forged chain"),
        "attestation": {
            "principal": latest["principal"],
            "db_session_principal": latest["db_session_principal"],
            "recorded_at": latest["recorded_at"],
            "seq": latest["seq"],
            "row_hash": latest["row_hash"],
            "payload_sha256": latest["payload_sha256"],
            "server_now": answer["server_now"],
            "anchored_at": anchor.get("anchored_at"),
            "last_reanchor": last_reanchor,
            "age_seconds": age,
            "freshness_window_seconds": window,
        },
        "census": payload,
    }
