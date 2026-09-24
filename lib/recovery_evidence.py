"""The verify re-read binding on V5-F08 recovery evidence (review G4, decided design).

A verify step RE-DERIVES a piece of evidence from provider and database reads,
then stamps it with

    verification = {"verifier": <registered id>, "verified_at": <UTC instant>,
                    "facts_digest": "sha256:" + sha256(canonical facts)}

where the facts are the evidence without its `verification` block, serialised
exactly as mcp-server/src/artifact-trust.js canonicalJson does. The evaluator
(mcp-server/src/recovery-matrix.v5.js) accepts a piece of evidence only when
the verifier is the one registered for its kind, the digest recomputes, and
verified_at is no more than 15 minutes before the evaluator's own clock — so
evidence must be re-verified at the time it is judged, and a file edited after
verification fails.

WHAT IT IS NOT: a signature. There is no signing key, so someone who
deliberately recomputes the digest can forge one. The binding stops stale
evidence, evidence edited after its re-read, and evidence that skipped the
re-read path; authenticating the verifier would need a key the repository does
not hold.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

VERIFIERS = {
    "restore_exercise": "tools/restore-watermark.py verify-receipt",
    "record_layer_rpo": "tools/pitr-restore-proof.py verify",
    "outbound_census": "tools/restore-watermark.py outbound-census",
}


def canonical_json(value: Any) -> str:
    """Byte-for-byte the same text as artifact-trust.js canonicalJson for JSON data."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def facts_digest(facts: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()


def bind(facts: dict[str, Any], kind: str, now: datetime | None = None) -> dict[str, Any]:
    """Return the facts with a fresh verification block. `facts` must not carry one already."""
    if "verification" in facts:
        raise ValueError("evidence already carries a verification block; re-derive it instead of re-stamping")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {**facts, "verification": {"verifier": VERIFIERS[kind], "verified_at": stamp,
                                      "facts_digest": facts_digest(facts)}}
