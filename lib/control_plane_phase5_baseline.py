"""Fixed-query, target-free Phase 5 cache baseline construction."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Protocol


class BaselineRefusal(ValueError):
    """Raised when immutable cache evidence cannot form a complete baseline."""


class Cursor(Protocol):
    def execute(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


# This is deliberately the only database query the baseline resolver issues.
# It derives the entire eligible attempt population from the ledger, then left
# joins immutable observation IDs/timestamps in the same [start,end) window.
CACHE_BASELINE_QUERY = """
with active_cache_contract as (
  select key
    from ops.cognition_job
   where active and cache_ttl_seconds > 0
   group by key
  having count(*) = 1
), expected as (
  select j.id::text,a.attempt,j.definition_key,j.definition_version,j.mode
    from ops.job j
    join ops.job_attempt a on a.job_id=j.id
    join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
    join active_cache_contract c on c.key=d.execution_contract->>'cognition_job'
   where d.execution_kind='cognition' and j.mode=%s
     and a.started_at >= %s and a.started_at < %s
)
select e.id,e.attempt,e.definition_key,e.definition_version,e.mode,
       o.id::text,o.cache_key,o.observation_kind,o.observed_at
  from expected e
  left join ops.cognition_cache_observation o
    on o.job_id=e.id::uuid and o.attempt=e.attempt
   and o.observed_at >= %s and o.observed_at < %s
 order by e.id,e.attempt,o.observed_at,o.id
"""

_KINDS = frozenset({"hit", "miss", "store", "invalidate", "invalidated", "expired"})
_READ_KINDS = frozenset({"hit", "miss", "expired", "invalidated"})


def _instant(value: object, label: str) -> datetime:
    if not isinstance(value, str): raise BaselineRefusal(f"{label} must be an RFC3339 instant")
    try: instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise BaselineRefusal(f"{label} must be an RFC3339 instant") from exc
    if instant.tzinfo is None: raise BaselineRefusal(f"{label} must include an offset")
    return instant.astimezone(timezone.utc)


def _attempt_key(row: dict[str, Any]) -> tuple[str, int, str, int, str]:
    try:
        job_id, workflow_key = row["job_id"], row["workflow_key"]
        if not isinstance(job_id, str) or not job_id.strip() or not isinstance(workflow_key, str) or not workflow_key.strip():
            raise BaselineRefusal("resolver row has null or blank immutable job/workflow identity")
        key = (job_id, int(row["attempt"]), workflow_key, int(row["workflow_version"]), str(row["mode"]))
    except (KeyError, TypeError, ValueError) as exc: raise BaselineRefusal("resolver row lacks immutable attempt identity") from exc
    if not key[0] or key[1] < 1 or not key[2] or key[3] < 1 or key[4] not in {"shadow","canary","live","replay"}:
        raise BaselineRefusal("resolver row has invalid immutable attempt identity")
    return key


def resolve_cache_baseline_rows(cursor: Cursor, *, start: str, end: str, mode: str) -> list[dict[str, Any]]:
    """Read only the fixed ledger/immutable-observation projection."""
    begin, finish = _instant(start, "window.start"), _instant(end, "window.end")
    if begin >= finish or mode not in {"shadow","canary","live","replay"}: raise BaselineRefusal("invalid baseline window or mode")
    cursor.execute(CACHE_BASELINE_QUERY, (mode, begin, finish, begin, finish))
    rows: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        if len(raw) != 9: raise BaselineRefusal("fixed baseline query returned an invalid row shape")
        job_id, attempt, workflow_key, workflow_version, row_mode, observation_id, cache_key, kind, observed_at = raw
        row = {"job_id": job_id, "attempt": attempt, "workflow_key": workflow_key, "workflow_version": workflow_version, "mode": row_mode,
               "observation_id": observation_id, "cache_key": cache_key, "observation_kind": kind, "observed_at": observed_at}
        _attempt_key(row)
        if row_mode != mode: raise BaselineRefusal("fixed resolver returned a different ledger mode")
        if observation_id is None:
            if any(value is not None for value in (cache_key, kind, observed_at)): raise BaselineRefusal("null observation identity has non-null evidence fields")
        elif not isinstance(observation_id, str) or not observation_id or not isinstance(cache_key, str) or not cache_key or not isinstance(kind, str):
            raise BaselineRefusal("immutable observation identity or fields are malformed")
        rows.append(row)
    return rows


def build_cache_baseline(contract: dict[str, Any], rows: list[dict[str, Any]], *, start: str, end: str, mode: str) -> dict[str, Any]:
    """Reduce only fixed-query resolver rows; never accepts caller completeness claims."""
    if contract.get("schema_version") != 1 or contract.get("kind") != "baseline_only": raise BaselineRefusal("unrecognized cache baseline contract")
    begin, finish = _instant(start, "window.start"), _instant(end, "window.end")
    if begin >= finish or mode not in {"shadow","canary","live","replay"}: raise BaselineRefusal("invalid baseline window or mode")
    expected: set[tuple[str,int,str,int,str]] = set(); reads: set[tuple[str,int,str,int,str]] = set(); ids: set[str] = set(); events: list[dict[str, Any]] = []; counts: Counter[str] = Counter()
    for row in rows:
        key = _attempt_key(row)
        if key[-1] != mode: raise BaselineRefusal("resolver row mode mismatch")
        expected.add(key)
        oid = row.get("observation_id")
        if oid is None: continue
        if not isinstance(oid, str) or not oid or oid in ids: raise BaselineRefusal("observation ID is missing or duplicated")
        ids.add(oid); cache_key, kind = row.get("cache_key"), row.get("observation_kind")
        if not isinstance(cache_key, str) or not cache_key or not isinstance(kind, str) or kind not in _KINDS: raise BaselineRefusal("observation fields are malformed")
        observed = row.get("observed_at")
        if isinstance(observed, datetime): instant = observed.astimezone(timezone.utc) if observed.tzinfo else None
        else: instant = _instant(observed, "observation.observed_at")
        if instant is None or not begin <= instant < finish: raise BaselineRefusal("observation is outside baseline window")
        normalized = "invalidated" if kind in {"invalidate","invalidated"} else kind
        if kind in _READ_KINDS:
            if key in reads: raise BaselineRefusal("attempt has more than one cache read observation")
            reads.add(key)
        counts[normalized] += 1; events.append({"id": oid, "attempt": key, "cache_key": cache_key, "kind": normalized, "observed_at": instant.isoformat().replace("+00:00","Z")})
    if not expected: raise BaselineRefusal("fixed resolver found no cache-eligible cognition attempts")
    if reads != expected: raise BaselineRefusal("fixed resolver population lacks immutable cache-read coverage")
    result = {"contract_version":1,"kind":"baseline_only","measure":"cognition_cache_events","window":{"start":begin.isoformat().replace("+00:00","Z"),"end":finish.isoformat().replace("+00:00","Z")},"mode":mode,"coverage":{"expected_attempts":len(expected),"observed_attempts":len(reads)},"event_counts":{k:counts[k] for k in ("hit","miss","store","expired","invalidated")},"target_evaluation":"not_permitted"}
    result["evidence_digest"] = sha256(json.dumps({"expected":sorted(expected),"observations":events},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return result
