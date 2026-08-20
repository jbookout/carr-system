"""Immutable-contract reduction for Phase 4 trusted partner continuity."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


class ContinuityRefusal(ValueError):
    pass


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuityRefusal("immutable continuity contract is unreadable") from exc
    if not isinstance(contract, dict) or contract.get("schema_version") != 1 or contract.get("contract_version") != 1:
        raise ContinuityRefusal("immutable continuity contract version is invalid")
    digest = contract.get("contract_digest")
    unsigned = {key: value for key, value in contract.items() if key != "contract_digest"}
    actual = sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not isinstance(digest, str) or digest != actual:
        raise ContinuityRefusal("immutable continuity contract digest does not match its content")
    if contract.get("tenant") != {"id": "carr-internal", "canonical_domain": "carr.us"}:
        raise ContinuityRefusal("continuity contract does not name the canonical tenant/domain")
    if contract.get("partners") != ["joe", "dell"]:
        raise ContinuityRefusal("continuity contract does not bind both canonical partners")
    if set(contract.get("streams", [])) != {
        "standing_context", "tentative_write_readback", "conflict_undo",
        "personal_canary_privacy_telemetry", "document_download",
    }:
        raise ContinuityRefusal("continuity contract has an unrecognized evidence stream")
    if contract.get("window") != {"minimum_overlap_seconds": 172800,
                                  "minimum_distinct_sessions_per_stream": 3,
                                  "maximum_cadence_gap_seconds": 86400}:
        raise ContinuityRefusal("continuity contract weakens the global window")
    if contract.get("retirement") != {"authority": "joe", "separate_from_continuity": True,
                                       "scheduler_receipt_reuse": "forbidden"}:
        raise ContinuityRefusal("Drive retirement contract is not Joe-only and separate")
    return contract


def _instant(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ContinuityRefusal(f"{label} lacks a timezone")
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        raise ContinuityRefusal(f"{label} is not an instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContinuityRefusal(f"{label} is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise ContinuityRefusal(f"{label} lacks a timezone")
    return parsed.astimezone(timezone.utc)


def evaluate_window(contract: Mapping[str, Any], rows: Iterable[tuple[Any, ...]]) -> dict[str, Any]:
    """Reduce only fixed SQL rows; no caller can claim completeness or freshness."""
    expected = {(partner, stream) for partner in contract["partners"] for stream in contract["streams"]}
    grouped: dict[tuple[str, str], list[tuple[datetime, datetime, str, str]]] = defaultdict(list)
    seen_rows = 0
    for raw in rows:
        if not isinstance(raw, (tuple, list)) or len(raw) != 8:
            raise ContinuityRefusal("fixed continuity query returned an invalid row shape")
        actor, stream, origin_session, origin_at, receiver_session, receiver_at, version, digest = raw
        key = (str(actor), str(stream))
        if key not in expected:
            raise ContinuityRefusal("fixed continuity query returned an unknown actor or stream")
        if int(version) != contract["contract_version"] or digest != contract["contract_digest"]:
            raise ContinuityRefusal("database continuity contract version/digest is not immutable-contract exact")
        origin = _instant(origin_at, "origin observed_at")
        receiver = _instant(receiver_at, "receiver observed_at")
        origin_id, receiver_id = str(origin_session), str(receiver_session)
        if not origin_id or not receiver_id or origin_id == receiver_id or receiver < origin:
            raise ContinuityRefusal("receiver evidence must be later and from a distinct session")
        grouped[key].append((origin, receiver, origin_id, receiver_id))
        seen_rows += 1
    if not seen_rows or set(grouped) != expected:
        missing = sorted(expected - set(grouped))
        raise ContinuityRefusal(f"fixed continuity evidence lacks required streams: {missing}")
    starts: list[datetime] = []
    ends: list[datetime] = []
    min_sessions = contract["window"]["minimum_distinct_sessions_per_stream"]
    max_gap = contract["window"]["maximum_cadence_gap_seconds"]
    for key, samples in grouped.items():
        samples.sort()
        if len({sample[2] for sample in samples}) < min_sessions or len({sample[3] for sample in samples}) < min_sessions:
            raise ContinuityRefusal(f"{key[0]}/{key[1]} lacks distinct origin and receiver sessions")
        origin_times = [sample[0] for sample in samples]
        if any((later - earlier).total_seconds() > max_gap for earlier, later in zip(origin_times, origin_times[1:])):
            raise ContinuityRefusal(f"{key[0]}/{key[1]} violates the maximum continuity cadence gap")
        starts.append(origin_times[0])
        ends.append(origin_times[-1])
    common_start, common_end = max(starts), min(ends)
    overlap = int((common_end - common_start).total_seconds())
    if overlap < contract["window"]["minimum_overlap_seconds"]:
        raise ContinuityRefusal("all ten streams do not share one real 48-hour bidirectional common window")
    return {"status": "CONTINUITY_PROVEN", "common_window": {
        "start": common_start.isoformat().replace("+00:00", "Z"),
        "end": common_end.isoformat().replace("+00:00", "Z"), "overlap_seconds": overlap,
    }, "stream_count": len(grouped)}
